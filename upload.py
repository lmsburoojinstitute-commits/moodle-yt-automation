import os
import datetime
import time
import google.auth
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
from google.oauth2.credentials import Credentials
import gspread
import io

# Load Secrets from GitHub Environment
CLIENT_ID = os.environ.get('CLIENT_ID')
CLIENT_SECRET = os.environ.get('CLIENT_SECRET')
REFRESH_TOKEN = os.environ.get('REFRESH_TOKEN')
SHEET_ID = os.environ.get('SHEET_ID')
FOLDER_ID = os.environ.get('FOLDER_ID')

def get_services():
    info = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    creds = Credentials.from_authorized_user_info(info)
    drive = build('drive', 'v3', credentials=creds)
    youtube = build('youtube', 'v3', credentials=creds)
    sheets = gspread.authorize(creds)
    return drive, youtube, sheets

def upload_video(youtube, file_path, title, playlist_id):
    body = {
        'snippet': {
            'title': title,
            'description': 'Moodle Automated Class Upload',
            'categoryId': '27' # Education
        },
        'status': {
            'privacyStatus': 'unlisted',
            'selfDeclaredMadeForKids': False
        }
    }
    
    # Large file upload (Resumable)
    media = MediaFileUpload(file_path, chunksize=1024*1024, resumable=True)
    request = youtube.videos().insert(part='snippet,status', body=body, media_body=media)
    
    video = None
    while video is None:
        status, video = request.next_chunk()
        if status:
            print(f"Uploaded {int(status.progress() * 100)}%")
            
    # Add to Playlist
    youtube.playlistItems().insert(
        part='snippet',
        body={
            'snippet': {
                'playlistId': playlist_id,
                'resourceId': {'kind': 'youtube#video', 'videoId': video['id']}
            }
        }
    ).execute()
    return video['id']

def set_thumbnail(youtube, video_id, thumb_path):
    youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(thumb_path)).execute()

def main():
    drive, youtube, sheets = get_services()
    sh = sheets.open_by_key(SHEET_ID)
    mapping_sheet = sh.worksheet("Mapping")
    log_sheet = sh.worksheet("UploadLog")
    
    mappings = mapping_sheet.get_all_records()
    logs = log_sheet.get_all_records()
    # Create a set of already uploaded file IDs for fast lookup
    uploaded_ids = {str(row['File ID']) for row in logs if row.get('Status') == 'UPLOADED'}

    # Get files from Drive
    query = f"'{FOLDER_ID}' in parents and mimeType='video/mp4' and trashed=false"
    results = drive.files().list(q=query, fields="files(id, name)").execute()
    files = results.get('files', [])

    if not files:
        print("No videos found in Drive folder.")
        return

    for f in files:
        file_id = f['id']
        file_name = f['name']
        
        if file_id in uploaded_ids:
            continue

        # Look for match in mapping (Identifier can be (1234) or meeting code)
        match = None
        for m in mappings:
            if str(m['Identifier']) in file_name:
                match = m
                break
        
        if match:
            print(f"Processing: {file_name}")
            try:
                # 1. Download from Drive to local runner disk
                request = drive.files().get_media(fileId=file_id)
                file_path = "video.mp4"
                with io.FileIO(file_path, 'wb') as fh:
                    downloader = MediaIoBaseDownload(fh, request)
                    done = False
                    while not done:
                        status, done = downloader.next_chunk()
                
                # 2. Upload to YouTube
                video_id = upload_video(youtube, file_path, file_name, match['Playlist ID'])
                
                # 3. Handle Thumbnail
                thumb_id = match.get('Thumbnail ID')
                if thumb_id:
                    request = drive.files().get_media(fileId=thumb_id)
                    thumb_path = "thumb.jpg"
                    with io.FileIO(thumb_path, 'wb') as tfh:
                        downloader = MediaIoBaseDownload(tfh, request)
                        done = False
                        while not done:
                            _, done = downloader.next_chunk()
                    set_thumbnail(youtube, video_id, thumb_path)
                    os.remove(thumb_path)

                # 4. Update Sheet Log
                # Find existing row to update or append new
                log_data = log_sheet.get_all_values()
                row_idx = -1
                for i, row in enumerate(log_data):
                    if row[0] == file_id:
                        row_idx = i + 1
                        break
                
                new_row = [file_id, file_name, "UPLOADED", str(datetime.datetime.now()), f"https://youtu.be/{video_id}"]
                if row_idx != -1:
                    log_sheet.update(f"A{row_idx}:E{row_idx}", [new_row])
                else:
                    log_sheet.append_row(new_row)
                
                print(f"Done: {file_name}")
                os.remove(file_path) # Clear disk space
                
            except Exception as e:
                print(f"Error with {file_name}: {e}")
        else:
            print(f"No match for {file_name}, skipping.")

if __name__ == "__main__":
    main()
