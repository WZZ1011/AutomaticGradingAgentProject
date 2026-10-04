from fastapi import FastAPI

from automaticgradingagentproject.api.markingapi import marking_router
from automaticgradingagentproject.api.userapi import userrouter

app=FastAPI()
app.include_router(userrouter)
app.include_router(marking_router)

