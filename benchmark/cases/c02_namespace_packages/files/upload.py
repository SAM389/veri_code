import google.auth
from google.cloud import storage

credentials, project = google.auth.default()
client = storage.Client(credentials=credentials, project=project)
