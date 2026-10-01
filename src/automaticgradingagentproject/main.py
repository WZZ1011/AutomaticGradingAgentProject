from fastapi import FastAPI

from automaticgradingagentproject.api.userapi import userrouter

app=FastAPI()
app.include_router(userrouter)

