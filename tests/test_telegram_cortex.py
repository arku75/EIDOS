from skills.plugins import telegram_cortex
import time

class MockBrain:
    def think(self, user_input, visual_context=None):
        return {"reply": f"Brain echoes: {user_input}", "action": "NONE"}
    
    def execute_action(self, action):
        print(f"Executing: {action}")

def test_telegram():
    print("📡 Testing Telegram Cortex...")
    brain = MockBrain()
    tele = telegram_cortex.TelegramCortex(brain)
    
    # Check Connectivity
    try:
        me = tele.get_updates()
        if me:
            print("✅ Telegram Connection established.")
            print(f"   API Response: {str(me)[:100]}...")  # pyre-ignore[arg-type]
        else:
            print("❌ Connections failed.")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    test_telegram()
