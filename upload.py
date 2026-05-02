import os
import datetime
import time
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
from google.oauth2.credentials import Credentials
import gspread
import io
import sys

# Load Secrets
CLIENT_ID = os.environ.get('CLIENT_ID')
CLIENT_SECRET = os.environ.get('CLIENT_SECRET')
REFRESH_TOKEN = os.environ.get('REFRESH_TOKEN')
SHEET_ID = os.environ.get('SHEET_ID')
FOLDER_ID = os.environ.get('FOLDER_ID')

def get_services():
    # Check if any secret is missing
    for var, val in [("CLIENT_ID", CLIENT_ID), ("CLIENT_SECRET", CLIENT_SECRET), ("REFRESH_TOKEN", REFRESH_TOKEN)]:
        if not val:
            print(f"ERROR: Missing GitHub Secret for {var}")
            sys.exit(1)
            
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
        'snippet': {'title': title, 'description': 'Moodle Automated Class Upload', 'categoryId': '27'},
        'status': {'privacyStatus': 'unlisted', 'selfDeclaredMadeForKids': False}
    }
    media = MediaFileUpload(file_path, chunksize=1024*1024, resumable=True)
    request = youtube.videos().insert(part='snippet,status', body=body, media_body=media)
    video = None
    while video is None:
        status, video = request.next_chunk()
        if status: print(f"Uploaded {int(status.progress() * 100)}%")
    
    # Add to Playlist
    youtube.playlistItems().insert(
        part='snippet', 
        body={'snippet': {'playlistId': playlist_id, 'resourceId': {'kind': 'youtube#video', 'videoId': video['id']}}}
    ).execute()
    return video['id']

def main():
    print("Starting process...")
    drive, youtube, sheets = get_services()
    
    try:
        sh = sheets.open_by_key(SHEET_ID)
        mapping_sheet = sh.worksheet("Mapping")
        log_sheet = sh.worksheet("UploadLog")
    except Exception as e:
        print(f"ERROR: Could not open Sheet or find tabs 'Mapping'/'UploadLog'. Details: {e}")
        sys.exit(1)

    mappings = mapping_sheet.get_all_records()
    logs = log_sheet.get_all_records()
    uploaded_ids = {str(row.get('File ID')) for row in logs if row.get('Status') == 'UPLOADED'}

    query = f"'{FOLDER_ID}' in parents and mimeType='video/mp4' and trashed=false"
    results = drive.files().list(q=query, fields="files(id, name)").execute()
    files = results.get('files', [])

    if not files:
        print("No new videos found in Drive folder.")
        return

    print(f"Found {len(files)} files in Drive. Checking for new ones...")

    for f in files:
        if f['id'] in uploaded_ids: continue
        
        # Match identifying text (ID in brackets or meeting code)
        match = next((m for m in mappings if str(m.get('Identifier')) in f['name']), None)
        
        if match:
            print(f"--- Processing: {f['name']} ---")
            print(f"Downloading from Drive...")
            request = drive.files().get_media(fileId=f['id'])
            with io.FileIO("video.mp4", 'wb') as fh:
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done: _, done = downloader.next_chunk()
            
            print(f"Uploading to YouTube Playlist: {match.get('Playlist ID')}...")
            try:
                video_id = upload_video(youtube, "video.mp4", f['name'], match.get('Playlist ID'))
                log_sheet.append_row([f['id'], f['name'], "UPLOADED", str(datetime.datetime.now()), f"https://youtu.be/{video_id}"])
                print(f"SUCCESS: {f['name']} uploaded as https://youtu.be/{video_id}")
            except Exception as yt_e:
                print(f"YOUTUBE ERROR: {yt_e}")
            
            if os.path.exists("video.mp4"): os.remove("video.mp4")
        else:
            print(f"Skipping: {f['name']} (No match in Mapping sheet)")

if __name__ == "__main__":
    main()
