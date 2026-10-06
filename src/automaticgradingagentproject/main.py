from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from automaticgradingagentproject.api.markingapi import marking_router
from automaticgradingagentproject.api.userapi import userrouter

app=FastAPI()
app.include_router(userrouter)
app.include_router(marking_router)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # 前端地址
    allow_credentials=True,
    allow_methods=["*"],    # 允许所有方法，包括 OPTIONS
    allow_headers=["*"],    # 允许所有头
)
