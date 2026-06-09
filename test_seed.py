import sys
from app.services.mongo import MongoService
from app.models.meeting_template import get_default_meeting_templates
import os

mongo = MongoService(os.getenv("MONGO_CONNECTION_STRING", "mongodb://localhost:27017"))
print("Initial Count:", len(mongo.get_all_meeting_templates()))
defaults = get_default_meeting_templates()
for template in defaults:
    existing = mongo.get_meeting_template(template["meeting_type_id"])
    if not existing:
        print("Upserting:", template["meeting_type_id"])
        mongo.update_meeting_template(template["meeting_type_id"], template)
print("Final Count:", len(mongo.get_all_meeting_templates()))
