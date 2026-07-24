"""
Manual smoke script to verify the configured NTC Gateway LLM works with NTC_API_KEY.
"""
import os
import sys
import re
import requests
from dotenv import load_dotenv
from pathlib import Path


DEFAULT_NTC_API_URL = "https://aigateway.ntictsolution.com/v1/chat/completions"
DEFAULT_NTC_MODEL = "ict-ollama/gemma4:31b-it-q4_K_M"


def clean_env_value(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip().strip('"').strip("'")


# Load environment variables
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

LLM_API_KEY = clean_env_value(os.getenv("NTC_API_KEY"))
LLM_API_URL = clean_env_value(os.getenv("NTC_API_URL")) or DEFAULT_NTC_API_URL
LLM_MODEL = clean_env_value(os.getenv("NTC_MODEL")) or DEFAULT_NTC_MODEL


def sanitize_gateway_error(text: str) -> str:
    """Redact API token details returned by the gateway."""
    sanitized = text or ""
    sanitized = re.sub(r"(Received API Key\s*=\s*)[^,\s]+", r"\1[redacted]", sanitized)
    sanitized = re.sub(r"(Key Hash \(Token\)\s*=\s*)[A-Fa-f0-9]+", r"\1[redacted]", sanitized)
    sanitized = re.sub(r"sk-[A-Za-z0-9._-]+", "sk-[redacted]", sanitized)
    return sanitized

def run_llm_gateway_smoke():
    """Test the configured LLM through the NTC Gateway API."""
    if not LLM_API_KEY:
        print("❌ Error: NTC_API_KEY not found in environment variables")
        return False

    print(f"📡 Testing configured LLM with NTC API Gateway...")
    print(f"   API URL: {LLM_API_URL}")
    print(f"   API Key: SET (env=NTC_API_KEY, len={len(LLM_API_KEY)})")
    print(f"   LLM Model: {LLM_MODEL}")

    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "You are a concise LLM assistant."
            },
            {
                "role": "user",
                "content": "สวัสดี ช่วยตอบสั้นๆ ว่า LLM ที่ตั้งค่าไว้ใช้งานได้"
            }
        ],
        "temperature": 0.5,
        "max_tokens": 100
    }

    try:
        print("\n⏳ Sending request...")
        response = requests.post(LLM_API_URL, headers=headers, json=payload, timeout=30)

        print(f"   Status Code: {response.status_code}")

        if response.status_code == 200:
            result = response.json()
            content = result["choices"][0]["message"]["content"]
            model_used = result.get("model", "unknown")
            print(f"\n✅ NTC Gateway LLM is working!")
            print(f"   Model Used: {model_used}")
            print(f"   Response: {content}")
            return True
        else:
            print(f"\n❌ API returned error:")
            print(f"   {sanitize_gateway_error(response.text)}")
            return False

    except requests.exceptions.Timeout:
        print("\n❌ Request timed out (30s)")
        return False
    except requests.exceptions.RequestException as e:
        print(f"\n❌ Request failed: {str(e)}")
        return False
    except (KeyError, IndexError) as e:
        print(f"\n❌ Failed to parse response: {str(e)}")
        print(f"   Raw response: {sanitize_gateway_error(response.text)}")
        return False

if __name__ == "__main__":
    success = run_llm_gateway_smoke()
    sys.exit(0 if success else 1)
