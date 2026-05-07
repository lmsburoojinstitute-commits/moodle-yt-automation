import os
import datetime
import time
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
from google.oauth2.credentials import Credentials
import gspread
import io

# Config
MIN_FILE_SIZE_MB = 5 

# Load Secrets from GitHub
CLIENT_ID = os.environ.get('CLIENT_ID')
CLIENT_SECRET = os.environ.get('CLIENT_SECRET')
REFRESH_TOKEN = os.environ.get('REFRESH_TOKEN')
SHEET_ID = os.environ.get('SHEET_ID')
FOLDER_ID = os.environ.get('FOLDER_ID')

def get_services():
    info = {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "refresh_token": REFRESH_TOKEN, "token_uri": "https://oauth2.googleapis.com/token"}
    creds = Credentials.from_authorized_user_info(info)
    return build('drive', 'v3', credentials=creds), build('youtube', 'v3', credentials=creds), gspread.authorize(creds)

def main():
    drive, youtube, sheets = get_services()
    sh = sheets.open_by_key(SHEET_ID)
    mapping_sheet = sh.worksheet("Mapping")
    log_sheet = sh.worksheet("UploadLog")
    
    # Get Mappings and filter out empty rows
    all_mappings = mapping_sheet.get_all_records()
    mappings = [m for m in all_mappings if str(m.get('Identifier', '')).strip() != ""]
    
    # Get Log and track what is already handled
    logs = log_sheet.get_all_records()
    # We skip anything that is NOT 'PENDING'
    handled_ids = {str(row['File ID']) for row in logs if row.get('Status') != 'PENDING'}

    query = f"'{FOLDER_ID}' in parents and mimeType='video/mp4' and trashed=false"
    results = drive.files().list(q=query, fields="files(id, name, size)").execute()
    files = results.get('files', [])

    print(f"Found {len(files)} total files in Drive. Checking mapping...")

    for f in files:
        file_id = f['id']
        file_name = f['name']
        file_size_mb = int(f.get('size', 0)) / (1024 * 1024)

        # 1. Skip if already processed (UPLOADED, SKIPPED, etc.)
        if file_id in handled_ids:
            continue

        # 2. Check Mapping First
        match = next((m for m in mappings if str(m['Identifier']) in file_name), None)
        
        if not match:
            print(f"Skipping: {file_name} (No Course ID found in Mapping sheet)")
            update_log(log_sheet, file_id, file_name, "SKIPPED_NO_MAPPING")
            continue

        # 3. Check Size
        if file_size_mb < MIN_FILE_SIZE_MB:
            print(f"Skipping: {file_name} (Too small: {round(file_size_mb, 2)} MB)")
            update_log(log_sheet, file_id, file_name, "SKIPPED_TOO_SMALL")
            continue

        # 4. If it passed all checks, START UPLOAD
        print(f">>> MATCH FOUND: Uploading {file_name} to Playlist {match['Playlist ID']}")
        try:
            # Download
            request = drive.files().get_media(fileId=file_id)
            with io.FileIO("video.mp4", 'wb') as fh:
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done: _, done = downloader.next_chunk()
            
            # YouTube Upload
            body = {'snippet': {'title': file_name, 'description': 'Moodle Automated Upload', 'categoryId': '27'}, 'status': {'privacyStatus': 'unlisted'}}
            media = MediaFileUpload("video.mp4", chunksize=1024*1024, resumable=True)
            video = youtube.videos().insert(part='snippet,status', body=body, media_body=media).execute()
            
            # Add to Playlist
            youtube.playlistItems().insert(part='snippet', body={'snippet': {'playlistId': match['Playlist ID'], 'resourceId': {'kind': 'youtube#video', 'videoId': video['id']}}}).execute()

            # Mark as Success
            update_log(log_sheet, file_id, file_name, "UPLOADED", f"https://youtu.be/{video['id']}")
            print(f"SUCCESS: {file_name}")
            os.remove("video.mp4")
            
        except Exception as e:
            print(f"ERROR processing {file_name}: {e}")
            # We don't mark as UPLOADED so it can retry tomorrow once, 
            # but if it's a Quota error, it will stop the whole script.
            if "quotaExceeded" in str(e):
                print("YouTube Daily Quota Hit. Stopping for today.")
                break

def update_log(sheet, file_id, name, status, link=""):
    # Finds the row with the File ID and updates it, or appends a new one
    try:
        cell = sheet.find(file_id)
        row = cell.row
        sheet.update(f"C{row}:E{row}", [[status, str(datetime.datetime.now()), link]])
    except:
        sheet.append_row([file_id, name, status, str(datetime.datetime.now()), link])

if __name__ == "__main__":
    main()
