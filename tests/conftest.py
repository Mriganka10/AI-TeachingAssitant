import os

os.environ["DATABASE_URL"] = "sqlite:///./data/test_professor_ai.db"
os.environ["LLM_SERVICE_MODE"] = "mock"
os.environ["OTP_DEV_MODE"] = "true"
os.environ["AUTH_ENABLED"] = "true"
os.environ["SECRET_KEY"] = "test-secret-key"
