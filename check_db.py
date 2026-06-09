import os
from pymongo import MongoClient

client = MongoClient(os.getenv("MONGO_CONNECTION_STRING", "mongodb://mongo:27017"))
db = client[os.getenv("MONGO_DB_NAME", "timsumv3")]
count = db.meeting_template.count_documents({})
print("TEMPLATE COUNT:", count)
for t in db.meeting_template.find():
    print(t.get("meeting_type_id"), t.get("thai_name"))
