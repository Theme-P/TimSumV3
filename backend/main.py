import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add project root to path to ensure imports work
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.services.pipeline import TranscribeSummaryPipeline
from app.models.meeting import get_meeting_types_menu, MEETING_TYPES
from app.utils.export import export_both
from app.utils.formatting import format_speaker

def main():
    # Get audio file from user
    audio_file = input("📁 กรุณาใส่ path ไฟล์เสียง: ").strip().strip('"').strip("'")
    
    if not os.path.exists(audio_file):
        print(f"❌ Error: File not found: {audio_file}")
        return
    
    # Show meeting type menu and get selection
    print()
    print(get_meeting_types_menu())
    
    while True:
        try:
            meeting_choice = input("🔢 เลือกประเภทการประชุม (0-11): ").strip()
            meeting_type_id = int(meeting_choice)
            if 0 <= meeting_type_id <= 11:
                break
            print("❌ กรุณาเลือก 0-11")
        except ValueError:
            print("❌ กรุณาใส่ตัวเลข 0-11")
    
    selected_type = MEETING_TYPES[meeting_type_id]
    print(f"\n✅ เลือก: {selected_type['thai']} ({selected_type['name']})")
    print(f"   โครงสร้าง: {selected_type['structure']}")
    print()
    
    # Run pipeline
    pipeline = TranscribeSummaryPipeline()
    output = pipeline.process(audio_file, meeting_type_id=meeting_type_id)
    pipeline.print_results(output)
    
    # Export to DOCX files (both transcript and summary)
    base_path = os.path.splitext(audio_file)[0]
    try:
        results = export_both(
            segments=output['full_transcript']['segments'],
            summary_text=output['summary'],
            base_path=base_path,
            audio_file=audio_file,
            audio_length=output['audio_length_seconds'],
            format_speaker_func=format_speaker,
            speaker_summary=output['full_transcript'].get('speaker_summary'),
            meeting_type_id=meeting_type_id
        )
        print(f"\n📄 Files exported:")
        print(f"   - Transcript: {results['transcript']}")
        print(f"   - Summary: {results['summary']}")
    except Exception as e:
        print(f"\n⚠️ Could not export DOCX: {e}")

if __name__ == "__main__":
    main()
