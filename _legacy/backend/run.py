"""
Run backend: uvicorn app.main:app --reload
For Windows Standalone: same command from packaged app dir.
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
