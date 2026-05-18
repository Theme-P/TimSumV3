"""
Test script to verify GPT-4.1 works with NTC API KEY
"""
import os
import sys
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

NTC_API_KEY = os.getenv("NTC_API_KEY")
NTC_API_URL = os.getenv("NTC_API_URL", "https://aigateway.ntictsolution.com/v1/chat/completions")

def test_gpt41():
    """Test GPT-4.1 API connection"""
    if not NTC_API_KEY:
        print("❌ Error: NTC_API_KEY not found in environment variables")
        return False
        
    print(f"📡 Testing GPT-4.1 with NTC API Gateway...")
    print(f"   API URL: {NTC_API_URL}")
    print(f"   API Key: {NTC_API_KEY[:10]}...{NTC_API_KEY[-4:]}")
    
    headers = {
        "Authorization": f"Bearer {NTC_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "gpt-4.1",
        "messages": [
            {
                "role": "system",
                "content": "You are a helpful assistant."
            },
            {
                "role": "user",
                "content": "สวัสดี ช่วยตอบสั้นๆ ว่า GPT-4.1 ใช้งานได้"
            }
        ],
        "temperature": 0.5,
        "max_tokens": 100
    }
    
    try:
        print("\n⏳ Sending request...")
        response = requests.post(NTC_API_URL, headers=headers, json=payload, timeout=30)
        
        print(f"   Status Code: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            content = result["choices"][0]["message"]["content"]
            model_used = result.get("model", "unknown")
            print(f"\n✅ GPT-4.1 is working!")
            print(f"   Model Used: {model_used}")
            print(f"   Response: {content}")
            return True
        else:
            print(f"\n❌ API returned error:")
            print(f"   {response.text}")
            return False
            
    except requests.exceptions.Timeout:
        print("\n❌ Request timed out (30s)")
        return False
    except requests.exceptions.RequestException as e:
        print(f"\n❌ Request failed: {str(e)}")
        return False
    except (KeyError, IndexError) as e:
        print(f"\n❌ Failed to parse response: {str(e)}")
        print(f"   Raw response: {response.text}")
        return False

if __name__ == "__main__":
    success = test_gpt41()
    sys.exit(0 if success else 1)
