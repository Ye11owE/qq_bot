"""
Agent 工具集 - 为团团提供网页访问、文件操作、文件收发等能力
v2.0: 新增 upload_file_to_user / download_user_file 工具
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

# ============ 文件访问配置 ============
PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_ALLOWED_DIRS = [PROJECT_DIR, Path("/tmp"), Path("/home")]
_configured_allowed_dirs = os.environ.get("QQBOT_ALLOWED_DIRS", "")
if _configured_allowed_dirs.strip():
    ALLOWED_DIRS = DEFAULT_ALLOWED_DIRS + [
        Path(item).expanduser()
        for item in _configured_allowed_dirs.split(os.pathsep)
        if item.strip()
    ]
else:
    ALLOWED_DIRS = DEFAULT_ALLOWED_DIRS
ALLOWED_DIR_DESCRIPTION = ", ".join(str(path) for path in ALLOWED_DIRS)
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
MAX_WEB_CONTENT_LEN = 8000
FILE_WORK_DIR = os.path.abspath(
    os.path.expanduser(os.environ.get("QQBOT_FILE_WORK_DIR") or str(Path("/tmp") / "qqbot_files"))
)

# 确保工作目录存在
os.makedirs(FILE_WORK_DIR, exist_ok=True)


def _is_safe_path(path: str) -> bool:
    try:
        resolved = Path(path).resolve()
        allowed_dirs = [Path(d).resolve() for d in ALLOWED_DIRS + [Path(FILE_WORK_DIR)]]
        return any(resolved == root or root in resolved.parents for root in allowed_dirs)
    except Exception:
        return False


def _is_within_file_work_dir(path: Path) -> bool:
    try:
        resolved = path.resolve()
        root = Path(FILE_WORK_DIR).resolve()
        return resolved == root or root in resolved.parents
    except Exception:
        return False


# ============ Bot 引用（由 chat.py 注入）============
_bot_instance = None

def set_bot(bot):
    global _bot_instance
    _bot_instance = bot

def get_bot():
    return _bot_instance


# ============ 工具定义（OpenAI function calling 格式）============
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "web_browse",
            "description": "访问指定URL的网页，返回页面标题和正文内容摘要。用于查询信息、阅读文章、获取数据等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要访问的网页URL，必须以 http:// 或 https:// 开头"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": f"读取指定路径的文本文件内容。仅允许读取 {ALLOWED_DIR_DESCRIPTION} 和文件工作目录下的文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件的绝对路径"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": f"将内容写入指定路径的文件（覆盖写入）。仅允许写入 {ALLOWED_DIR_DESCRIPTION} 和文件工作目录。会自动创建父目录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件的绝对路径"},
                    "content": {"type": "string", "description": "要写入的文件内容"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": f"列出指定目录下的文件和子目录。仅允许列出 {ALLOWED_DIR_DESCRIPTION} 和文件工作目录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目录的绝对路径"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "upload_file_to_user",
            "description": f"将服务器上的文件发送给用户。当用户要求修改文件后返回、或生成了新文件需要发给用户时使用此工具。文件必须位于 {FILE_WORK_DIR} 目录下。",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": f"要发送的文件绝对路径，必须在 {FILE_WORK_DIR}/ 下"},
                    "user_id": {"type": "string", "description": "接收文件的用户QQ号"},
                    "group_id": {"type": "string", "description": "如果是群聊则填群号，私聊则留空"}
                },
                "required": ["file_path", "user_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "使用搜索引擎搜索关键词，返回多条搜索结果（标题+摘要+链接）。当用户问到实时信息、新闻、价格、天气、知识性问题时，优先使用此工具获取最新信息，再用 web_browse 深入阅读具体页面。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词，简洁精准"},
                    "num_results": {"type": "integer", "description": "返回结果数量，默认5，最大10"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_code",
            "description": "在安全沙箱中执行Python代码并返回输出。适用于数学计算、数据处理、格式转换、逻辑验证等任务。禁止执行网络请求、文件读写、系统命令等危险操作。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "要执行的Python代码"}
                },
                "required": ["code"]
            }
        }
    }
]


# ============ 工具实现 ============
async def web_browse(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        return json.dumps({"error": "URL必须以 http:// 或 https:// 开头"}, ensure_ascii=False)
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
                )
                page = await context.new_page()
                await page.goto(url, timeout=20000, wait_until="domcontentloaded")
                await asyncio.sleep(2)
                title = await page.title()
                content = await page.evaluate("""
                    () => {
                        document.querySelectorAll('script, style, nav, footer, header').forEach(el => el.remove());
                        return document.body ? document.body.innerText : '';
                    }
                """)
                content = content.strip()[:MAX_WEB_CONTENT_LEN]
                return json.dumps({"title": title, "url": url, "content": content}, ensure_ascii=False)
            finally:
                await browser.close()
    except Exception as e:
        return json.dumps({"error": f"网页访问失败: {str(e)}"}, ensure_ascii=False)


async def read_file(path: str) -> str:
    if not _is_safe_path(path):
        return json.dumps({"error": f"不允许访问该路径"}, ensure_ascii=False)
    try:
        p = Path(path)
        if not p.exists():
            return json.dumps({"error": f"文件不存在: {path}"}, ensure_ascii=False)
        if p.stat().st_size > MAX_FILE_SIZE:
            return json.dumps({"error": f"文件过大（>{MAX_FILE_SIZE}字节）"}, ensure_ascii=False)
        content = p.read_text(encoding="utf-8", errors="replace")
        return json.dumps({"path": path, "content": content}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"读取失败: {str(e)}"}, ensure_ascii=False)


async def write_file(path: str, content: str) -> str:
    if not _is_safe_path(path):
        return json.dumps({"error": f"不允许写入该路径"}, ensure_ascii=False)
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return json.dumps({"success": True, "path": path, "bytes_written": len(content.encode("utf-8"))}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"写入失败: {str(e)}"}, ensure_ascii=False)


async def list_directory(path: str) -> str:
    if not _is_safe_path(path):
        return json.dumps({"error": f"不允许访问该路径"}, ensure_ascii=False)
    try:
        p = Path(path)
        if not p.is_dir():
            return json.dumps({"error": f"不是目录: {path}"}, ensure_ascii=False)
        entries = [{"name": i.name, "type": "dir" if i.is_dir() else "file",
                     "size": i.stat().st_size if i.is_file() else None}
                    for i in sorted(p.iterdir())]
        return json.dumps({"path": path, "entries": entries}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"列目录失败: {str(e)}"}, ensure_ascii=False)


async def upload_file_to_user(file_path: str, user_id: str, group_id: str = "") -> str:
    """通过 OneBot API 将文件发送给用户"""
    bot = get_bot()
    if not bot:
        return json.dumps({"error": "Bot 未连接，无法发送文件"}, ensure_ascii=False)

    fp = Path(file_path)
    if not fp.exists():
        return json.dumps({"error": f"文件不存在: {file_path}"}, ensure_ascii=False)
    if not _is_within_file_work_dir(fp):
        return json.dumps({"error": "文件路径不在允许范围内"}, ensure_ascii=False)

    file_size = fp.stat().st_size
    if file_size > MAX_FILE_SIZE:
        return json.dumps({"error": f"文件过大（{file_size}字节），最大允许{MAX_FILE_SIZE}字节"}, ensure_ascii=False)

    try:
        if group_id and group_id != "0":
            # 群文件上传
            result = await bot.call_api(
                "upload_group_file",
                group_id=int(group_id),
                file=str(fp.resolve()),
                name=fp.name,
            )
            return json.dumps({"success": True, "method": "group", "file": fp.name, "group_id": group_id}, ensure_ascii=False)
        else:
            # 私聊文件上传
            result = await bot.call_api(
                "upload_private_file",
                user_id=int(user_id),
                file=str(fp.resolve()),
                name=fp.name,
            )
            return json.dumps({"success": True, "method": "private", "file": fp.name, "user_id": user_id}, ensure_ascii=False)
    except Exception as e:
        error_msg = str(e)
        # NapCat 可能不支持某些 API，给出明确提示
        if "unsupported" in error_msg.lower() or "not implemented" in error_msg.lower():
            return json.dumps({"error": f"当前 QQ 协议端不支持文件上传API: {error_msg}"}, ensure_ascii=False)
        return json.dumps({"error": f"文件上传失败: {error_msg}"}, ensure_ascii=False)



# ============ 新增工具实现 ============

async def web_search(query: str, num_results: int = 5) -> str:
    """使用 DuckDuckGo HTML 搜索（无需API Key）"""
    num_results = min(max(num_results, 1), 10)
    try:
        import httpx
        from urllib.parse import quote_plus
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        }
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return json.dumps({"error": f"搜索请求失败 HTTP {resp.status_code}"}, ensure_ascii=False)
            html = resp.text

        results = []
        import re as _re
        links = _re.findall(r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>', html, _re.DOTALL)
        snippets = _re.findall(r'class="result__snippet">(.*?)</[at]', html, _re.DOTALL)

        for i in range(min(len(links), num_results)):
            href = links[i][0]
            title = _re.sub(r'<[^>]+>', '', links[i][1]).strip()
            snippet = _re.sub(r'<[^>]+>', '', snippets[i]).strip() if i < len(snippets) else ""
            if title and href:
                results.append({"title": title, "url": href, "snippet": snippet})

        if not results:
            return json.dumps({"query": query, "results": [], "message": "未找到相关结果，请尝试更换关键词"}, ensure_ascii=False)

        return json.dumps({"query": query, "count": len(results), "results": results}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"搜索失败: {str(e)}"}, ensure_ascii=False)


async def run_code(code: str) -> str:
    """在独立进程中运行短时 Python 计算代码。"""
    forbidden = [
        "import os", "import subprocess", "import shutil",
        "import socket", "import requests", "import httpx",
        "open(", "__import__", "exec(", "eval(",
        "os.system", "os.popen", "subprocess.",
        "import ctypes", "import pty", "import tty",
    ]
    code_lower = code.lower()
    for fb in forbidden:
        if fb.lower() in code_lower:
            return json.dumps({"error": f"代码包含禁止的操作: {fb}，仅允许纯计算和数据处理"}, ensure_ascii=False)

    if len(code) > 50000:
        return json.dumps({"error": "代码过长，最多允许 50000 个字符"}, ensure_ascii=False)

    worker_code = r'''import contextlib
import datetime
import functools
import io
import itertools
import json
import math
import re
import sys

code = sys.stdin.read()
stdout_capture = io.StringIO()
stderr_capture = io.StringIO()
safe_builtins = {
    "abs": abs, "all": all, "any": any, "bin": bin, "bool": bool,
    "chr": chr, "dict": dict, "divmod": divmod, "enumerate": enumerate,
    "filter": filter, "float": float, "format": format, "frozenset": frozenset,
    "hex": hex, "int": int, "isinstance": isinstance, "len": len, "list": list,
    "map": map, "max": max, "min": min, "oct": oct, "ord": ord, "pow": pow,
    "print": print, "range": range, "repr": repr, "reversed": reversed,
    "round": round, "set": set, "slice": slice, "sorted": sorted,
    "str": str, "sum": sum, "tuple": tuple, "type": type, "zip": zip,
    "True": True, "False": False, "None": None,
}
safe_globals = {
    "__builtins__": safe_builtins,
    "math": math, "json": json, "datetime": datetime,
    "collections": __import__("collections"), "itertools": itertools,
    "functools": functools, "re": re,
}

try:
    with contextlib.redirect_stdout(stdout_capture), contextlib.redirect_stderr(stderr_capture):
        exec(code, safe_globals, {})
    stdout_value = stdout_capture.getvalue().strip()
    stderr_value = stderr_capture.getvalue().strip()
    result = {}
    if stdout_value:
        result["stdout"] = stdout_value[:4000]
    if stderr_value:
        result["stderr"] = stderr_value[:2000]
    if not stdout_value and not stderr_value:
        result["message"] = "代码执行成功，但无输出"
except Exception as error:
    result = {"error": f"代码执行失败: {error}"}

print(json.dumps(result))
'''

    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-c",
            worker_code,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(code.encode("utf-8")), timeout=15
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return json.dumps({"error": "代码执行超过 15 秒，已停止运行"}, ensure_ascii=False)

        if process.returncode != 0:
            error = stderr.decode("utf-8", errors="replace").strip()
            return json.dumps({"error": error or "代码执行进程异常退出"}, ensure_ascii=False)
        output = stdout.decode("utf-8", errors="replace").strip()
        try:
            return json.dumps(json.loads(output), ensure_ascii=False)
        except json.JSONDecodeError:
            return json.dumps({"error": "代码执行结果格式异常"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"代码执行失败: {str(e)}"}, ensure_ascii=False)


# ============ 文件下载辅助函数（供 chat.py 调用）============
async def download_user_file(
    bot, url: str, filename: str, file_id: str = ""
) -> Optional[str]:
    """从 OneBot 的 URL 或本地路径下载用户发送的文件到工作目录。"""
    safe_name = "".join(c for c in filename if c.isalnum() or c in "._-").strip()
    if not safe_name:
        safe_name = f"file_{int(time.time())}"
    local_path = os.path.join(FILE_WORK_DIR, safe_name)

    # 避免重名
    counter = 1
    base, ext = os.path.splitext(local_path)
    while os.path.exists(local_path):
        local_path = f"{base}_{counter}{ext}"
        counter += 1

    source = (url or "").strip()
    if not source and file_id:
        try:
            result = await bot.call_api("get_file", file_id=file_id)
            data = result.get("data", result) if isinstance(result, dict) else {}
            for key in ("url", "file", "file_path", "path"):
                candidate = data.get(key)
                if candidate:
                    source = str(candidate).strip()
                    break
        except Exception as e:
            print(f"[FileTool] 获取文件信息失败: {e}")

    if not source:
        print(f"[FileTool] OneBot 未提供文件来源: file_id={file_id}")
        return None

    try:
        if source.startswith(("http://", "https://")):
            import httpx

            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                async with client.stream("GET", source) as resp:
                    if resp.status_code != 200:
                        print(f"[FileTool] 下载失败 HTTP {resp.status_code}")
                        return None
                    content_length = resp.headers.get("content-length")
                    if content_length and int(content_length) > MAX_FILE_SIZE:
                        print(f"[FileTool] 文件过大: {content_length} bytes")
                        return None
                    content_buffer = bytearray()
                    async for chunk in resp.aiter_bytes():
                        if len(content_buffer) + len(chunk) > MAX_FILE_SIZE:
                            print(f"[FileTool] 文件超过 {MAX_FILE_SIZE} bytes 上限")
                            return None
                        content_buffer.extend(chunk)
                    content = bytes(content_buffer)
        else:
            if source.startswith("file://"):
                source = source[7:]
            source_path = Path(os.path.expanduser(source))
            if not source_path.is_file():
                print(f"[FileTool] 本地文件不存在: {source}")
                return None
            if source_path.stat().st_size > MAX_FILE_SIZE:
                print(f"[FileTool] 文件过大: {source_path.stat().st_size} bytes")
                return None
            content = source_path.read_bytes()

        if len(content) > MAX_FILE_SIZE:
            print(f"[FileTool] 文件过大: {len(content)} bytes")
            return None
        Path(local_path).write_bytes(content)
        print(f"[FileTool] 已下载: {filename} -> {local_path} ({len(content)} bytes)")
        return local_path
    except Exception as e:
        print(f"[FileTool] 下载异常: {e}")
        return None


# ============ 工具调度器 ============
TOOL_DISPATCH = {
    "web_browse": web_browse,
    "read_file": read_file,
    "write_file": write_file,
    "list_directory": list_directory,
    "upload_file_to_user": upload_file_to_user,
    "web_search": web_search,
    "run_code": run_code,
}


async def execute_tool(name: str, arguments: dict) -> str:
    func = TOOL_DISPATCH.get(name)
    if not func:
        return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
    try:
        return await func(**arguments)
    except TypeError as e:
        return json.dumps({"error": f"工具参数错误: {str(e)}"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"工具执行异常: {str(e)}"}, ensure_ascii=False)
