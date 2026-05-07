import os
import datetime
import time
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
from google.oauth2.credentials import Credentials
import gspread
import io

# Config
MIN_FILE_SIZE_MB = 5 # Skip files smaller than 5MB

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
    mappings = mapping_sheet.get_all_records()
    logs = log_sheet.get_all_records()
    uploaded_ids = {str(row['File ID']) for row in logs if row.get('Status') == 'UPLOADED'}

    query = f"'{FOLDER_ID}' in parents and mimeType='video/mp4' and trashed=false"
    results = drive.files().list(q=query, fields="files(id, name, size)").execute()
    files = results.get('files', [])

    for f in files:
        file_id = f['id']
        file_name = f['name']
        file_size_mb = int(f.get('size', 0)) / (1024 * 1024)

        if file_id in uploaded_ids: continue

        # --- NEW SIZE FILTER ---
        if file_size_mb < MIN_FILE_SIZE_MB:
            print(f"Skipping garbage/test file: {file_name} ({round(file_size_mb, 2)} MB)")
            continue

        match = next((m for m in mappings if str(m['Identifier']) in file_name), None)
        if match:
            print(f"Processing: {file_name}")
            try:
                # Download
                request = drive.files().get_media(fileId=file_id)
                with io.FileIO("video.mp4", 'wb') as fh:
                    downloader = MediaIoBaseDownload(fh, request)
                    done = False
                    while not done: _, done = downloader.next_chunk()
                
                # Upload
                body = {'snippet': {'title': file_name, 'description': 'Moodle Automated Upload', 'categoryId': '27'}, 'status': {'privacyStatus': 'unlisted'}}
                media = MediaFileUpload("video.mp4", chunksize=1024*1024, resumable=True)
                video = youtube.videos().insert(part='snippet,status', body=body, media_body=media).execute()
                
                # Add to Playlist (This was the error part)
                try:
                    youtube.playlistItems().insert(part='snippet', body={'snippet': {'playlistId': match['Playlist ID'], 'resourceId': {'kind': 'youtube#video', 'videoId': video['id']}}}).execute()
                except Exception as pl_e:
                    print(f"Video uploaded but Playlist failed: {pl_e}")

                # Log Success
                log_sheet.append_row([file_id, file_name, "UPLOADED", str(datetime.datetime.now()), f"https://youtu.be/{video['id']}"])
                print(f"Success: {file_name}")
                os.remove("video.mp4")
            except Exception as e:
                print(f"Error: {e}")

if __name__ == "__main__":
    main()
