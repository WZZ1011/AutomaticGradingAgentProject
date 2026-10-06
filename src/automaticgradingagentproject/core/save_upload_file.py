import os
import uuid

from fastapi import UploadFile

UPLOAD_DIR = "d:/marking_uploads"

def save_upload_file(file: UploadFile) -> str:
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    # 用 uuid 避免重名覆盖
    ext = os.path.splitext(file.filename or "")[1] # filename 类型是 str | None，用 or "" 收窄成 str，才能传给 splitext
    filename = f"{uuid.uuid4().hex}{ext}"
    path = os.path.abspath(os.path.join(UPLOAD_DIR, filename))
    with open(path, "wb") as f:
        f.write(file.file.read())  #当前直接同步写，先不考虑异步，也先不考虑传入大小
    return path