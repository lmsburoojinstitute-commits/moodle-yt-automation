import os
import datetime
import time
import re
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
from google.oauth2.credentials import Credentials
import gspread
import io

# Config
MIN_FILE_SIZE_MB = 20 

# Load Secrets
CLIENT_ID = os.environ.get('CLIENT_ID')
CLIENT_SECRET = os.environ.get('CLIENT_SECRET')
REFRESH_TOKEN = os.environ.get('REFRESH_TOKEN')
SHEET_ID = os.environ.get('SHEET_ID')
FOLDER_ID = os.environ.get('FOLDER_ID')

def get_services():
    info = {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "refresh_token": REFRESH_TOKEN, "token_uri": "https://oauth2.googleapis.com/token"}
    creds = Credentials.from_authorized_user_info(info)
    return build('drive', 'v3', credentials=creds), build('youtube', 'v3', credentials=creds), gspread.authorize(creds)

def get_next_class_number(youtube, playlist_id):
    """Counts videos in playlist and returns next number."""
    try:
        res = youtube.playlists().list(part='contentDetails', id=playlist_id).execute()
        count = res['items'][0]['contentDetails']['itemCount']
        return count + 1
    except:
        return 1

def format_date_from_filename(filename):
    """Extracts YYYY/MM/DD from filename and returns (DD.Month)"""
    match = re.search(r'(\d{4})/(\d{2})/(\d{2})', filename)
    if match:
        year, month_num, day = match.groups()
        month_obj = datetime.datetime.strptime(month_num, "%m")
        month_name = month_obj.strftime("%b") # e.g., "May"
        return f"({day}.{month_name})"
    return ""

def main():
    drive, youtube, sheets = get_services()
    sh = sheets.open_by_key(SHEET_ID)
    mapping_sheet = sh.worksheet("Mapping")
    log_sheet = sh.worksheet("UploadLog")
    
    mappings = mapping_sheet.get_all_records()
    logs = log_sheet.get_all_records()
    handled_ids = {str(row['File ID']) for row in logs if row.get('Status') != 'PENDING'}

    query = f"'{FOLDER_ID}' in parents and mimeType='video/mp4' and trashed=false"
    results = drive.files().list(q=query, fields="files(id, name, size)").execute()
    files = results.get('files', [])

    for f in files:
        file_id = f['id']
        file_name = f['name']
        file_size_mb = int(f.get('size', 0)) / (1024 * 1024)

        if file_id in handled_ids: continue

        # Match Mapping
        match = next((m for m in mappings if str(m['Identifier']) in file_name), None)
        if not match:
            continue

        if file_size_mb < MIN_FILE_SIZE_MB:
            continue

        print(f"Processing: {file_name}")
        try:
            # 1. Generate the New Professional Title
            date_part = format_date_from_filename(file_name)
            class_num = get_next_class_number(youtube, match['Playlist ID'])
            # Format: (05.May) Alim/Alimah Year 4 Summer Semester 26-27 | Class 15
            new_title = f"{date_part} {match['Display Name']} {match['Suffix']} | Class {class_num}"
            
            # 2. Download
            request = drive.files().get_media(fileId=file_id)
            with io.FileIO("video.mp4", 'wb') as fh:
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done: _, done = downloader.next_chunk()
            
            # 3. Upload to YouTube
            body = {'snippet': {'title': new_title, 'description': 'Automated Upload', 'categoryId': '27'}, 'status': {'privacyStatus': 'unlisted'}}
            media = MediaFileUpload("video.mp4", chunksize=1024*1024, resumable=True)
            video = youtube.videos().insert(part='snippet,status', body=body, media_body=media).execute()
            video_id = video['id']
            
            # 4. Add to Playlist
            youtube.playlistItems().insert(part='snippet', body={'snippet': {'playlistId': match['Playlist ID'], 'resourceId': {'kind': 'youtube#video', 'videoId': video_id}}}).execute()

            # 5. Set Thumbnail
            thumb_id = match.get('Thumbnail ID')
            if thumb_id:
                try:
                    t_request = drive.files().get_media(fileId=thumb_id)
                    with io.FileIO("thumb.jpg", 'wb') as tfh:
                        t_downloader = MediaIoBaseDownload(tfh, t_request)
                        t_done = False
                        while not t_done: _, t_done = t_downloader.next_chunk()
                    youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload("thumb.jpg")).execute()
                    os.remove("thumb.jpg")
                except Exception as te:
                    print(f"Thumbnail failed: {te}")

            # 6. Log Success
            try:
                cell = log_sheet.find(file_id)
                log_sheet.update(f"C{cell.row}:E{cell.row}", [["UPLOADED", str(datetime.datetime.now()), f"https://youtu.be/{video_id}"]])
            except:
                log_sheet.append_row([file_id, file_name, "UPLOADED", str(datetime.datetime.now()), f"https://youtu.be/{video_id}"])
            
            print(f"Successfully uploaded: {new_title}")
            os.remove("video.mp4")

        except Exception as e:
            print(f"Error: {e}")
            if "quotaExceeded" in str(e): break

if __name__ == "__main__":
    main()
