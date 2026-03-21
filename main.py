import os

import uvicorn
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from app.api import auth, webhook, chat_session, chat_message, knowledge_base, document, user, monitoring

app = FastAPI(title="auto-sre", version="1.0.0")

app.include_router(auth.router, prefix='/api/v1/auth', tags=["auth"])
app.include_router(webhook.router, prefix='/webhook', tags=["webhook"])
app.include_router(chat_session.router, prefix='/api/v1/chat', tags=["chat"])
app.include_router(chat_message.router, prefix='/api/v1/chat', tags=["chat"])
app.include_router(knowledge_base.router, prefix='/api/v1/rag', tags=["rag"])
app.include_router(document.router, prefix='/api/v1/rag', tags=["rag"])
app.include_router(user.router, prefix='/api/v1/users', tags=["users"])
app.include_router(monitoring.router, tags=["monitoring"])

app.add_middleware(
CORSMiddleware,
allow_origins=["*"],
allow_credentials=True,
allow_methods=["*"],
allow_headers=["*"],
)

def main():
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    main()
