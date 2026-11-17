import os
import posixpath
import uuid
from dataclasses import dataclass

from fastapi import UploadFile

UPLOAD_DIR = "d:/marking_uploads"


@dataclass(frozen=True, slots=True)
class SavedUpload:
    """落盘结果：path 是磁盘绝对路径，file_name 是给用户看的原始文件名。"""

    path: str
    file_name: str


def sanitize_filename(raw: str | None, fallback: str) -> str:
    """把上传文件名清理成可安全展示的名字。

    只用于入库/展示/Content-Disposition，绝不参与路径拼接。
    """
    name = posixpath.basename((raw or "").replace("\\", "/")).strip()
    name = "".join(ch for ch in name if ch.isprintable())
    if name in {"", ".", ".."}:
        return fallback
    if len(name) > 255:
        stem, ext = posixpath.splitext(name)
        name = stem[: 255 - len(ext)] + ext
    return name


def save_upload_file(file: UploadFile) -> SavedUpload:
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    # 用 uuid 避免重名覆盖；扩展名只取自原始文件名的 basename
    raw_name = posixpath.basename((file.filename or "").replace("\\", "/"))
    ext = posixpath.splitext(raw_name)[1]
    stored_name = f"{uuid.uuid4().hex}{ext}"
    file_name = sanitize_filename(file.filename, fallback=stored_name)
    path = os.path.abspath(os.path.join(UPLOAD_DIR, stored_name))
    with open(path, "wb") as f:
        f.write(file.file.read())  #当前直接同步写，先不考虑异步，也先不考虑传入大小
    return SavedUpload(path=path, file_name=file_name)
