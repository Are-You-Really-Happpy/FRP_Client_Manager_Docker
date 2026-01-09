import os
import subprocess
import signal
import sys
import time
import glob
import secrets
import string
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import toml

# 全局变量
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRPC_BIN = os.path.join(BASE_DIR, "../frpc")
CONFIG_DIR = os.path.join(BASE_DIR, "../")
STATIC_DIR = os.path.join(BASE_DIR, "static")
DEFAULT_CONFIG = "frpc.toml"
LOG_FILE = os.path.join(CONFIG_DIR, "frpc.log")
active_config = DEFAULT_CONFIG
frpc_process = None

# 管理员认证设置
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
PASSWORD_FILE = os.path.join(CONFIG_DIR, "admin_password.txt")

if not ADMIN_PASSWORD:
    # 尝试从文件读取已保存的密码
    if os.path.exists(PASSWORD_FILE):
        try:
            with open(PASSWORD_FILE, "r") as f:
                for line in f:
                    if line.startswith("Password: "):
                        ADMIN_PASSWORD = line.replace("Password: ", "").strip()
                        break
        except Exception as e:
            print(f"Warning: Failed to read password from file: {e}")

if not ADMIN_PASSWORD:
    # 如果没有环境变量也没有通过文件找到密码，则生成新的
    alphabet = string.ascii_letters + string.digits
    ADMIN_PASSWORD = ''.join(secrets.choice(alphabet) for i in range(16))
    print(f"\n{'='*50}")
    print(f"Admin Authentication Credentials (NEWLY GENERATED):")
    print(f"Username: {ADMIN_USERNAME}")
    print(f"Password: {ADMIN_PASSWORD}")
    print(f"{'='*50}\n")

    # 保存新生成的密码
    try:
        with open(PASSWORD_FILE, "w") as f:
            f.write(f"Username: {ADMIN_USERNAME}\nPassword: {ADMIN_PASSWORD}\n")
    except Exception as e:
        print(f"Warning: Failed to save password to file: {e}")

security = HTTPBasic()

def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    is_correct_username = secrets.compare_digest(credentials.username, ADMIN_USERNAME)
    is_correct_password = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (is_correct_username and is_correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

@asynccontextmanager
async def lifespan(app: FastAPI):
    start_frpc()
    yield
    stop_frpc()

app = FastAPI(lifespan=lifespan, dependencies=[Depends(verify_credentials)])

# 允许跨域请求（开发环境）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ConfigUpdate(BaseModel):
    filename: str
    content: str

class CreateConfig(BaseModel):
    filename: str

class RunConfig(BaseModel):
    filename: str

def start_frpc():
    global frpc_process, active_config
    if frpc_process:
        stop_frpc()
    
    print(f"正在启动 frpc，使用配置文件: {active_config}...")
    config_path = os.path.join(CONFIG_DIR, active_config)
    
    try:
        # 确保二进制文件可执行
        if not os.access(FRPC_BIN, os.X_OK):
            os.chmod(FRPC_BIN, 0o755)
            
        # 打开日志文件
        log_fd = open(LOG_FILE, "w")
        
        frpc_process = subprocess.Popen(
            [FRPC_BIN, "-c", config_path],
            stdout=log_fd,
            stderr=subprocess.STDOUT
        )
        log_fd.close()
    except Exception as e:
        print(f"启动 frpc 失败: {e}")

def stop_frpc():
    global frpc_process
    if frpc_process:
        print("正在停止 frpc...")
        frpc_process.terminate()
        try:
            frpc_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            frpc_process.kill()
        frpc_process = None

@app.get("/api/logs")
async def get_logs():
    """获取 frpc 运行日志"""
    if not os.path.exists(LOG_FILE):
        return {"logs": "暂无日志"}
    try:
        with open(LOG_FILE, "r") as f:
            return {"logs": f.read()}
    except Exception as e:
        return {"logs": f"读取日志失败: {e}"}

@app.get("/api/configs")
async def list_configs():
    """列出所有 .toml 配置文件"""
    try:
        files = glob.glob(os.path.join(CONFIG_DIR, "*.toml"))
        configs = [os.path.basename(f) for f in files]
        return {"configs": configs, "active": active_config}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/configs")
async def create_config(data: CreateConfig):
    """创建新的配置文件"""
    filename = data.filename
    if not filename.endswith(".toml"):
        filename += ".toml"
    
    filepath = os.path.join(CONFIG_DIR, filename)
    if os.path.exists(filepath):
        raise HTTPException(status_code=400, detail="文件已存在")
    
    try:
        # 创建一个空的默认配置
        default_content = """# 新建配置文件
serverAddr = "127.0.0.1"
serverPort = 7000
"""
        with open(filepath, "w") as f:
            f.write(default_content)
        return {"status": "success", "message": f"配置文件 {filename} 已创建"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/config")
async def get_config(filename: str):
    """获取指定配置文件的内容"""
    filepath = os.path.join(CONFIG_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="文件不存在")
        
    try:
        with open(filepath, "r") as f:
            return {"content": f.read()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/config")
async def update_config(config: ConfigUpdate):
    """更新指定配置文件的内容"""
    filepath = os.path.join(CONFIG_DIR, config.filename)
    try:
        # 验证 TOML 格式
        toml.loads(config.content)
        
        with open(filepath, "w") as f:
            f.write(config.content)
        
        # 如果更新的是当前正在运行的配置，则重启 frpc
        if config.filename == active_config:
            start_frpc()
            return {"status": "success", "message": "配置已更新并重启 frpc"}
        else:
            return {"status": "success", "message": "配置已保存"}
            
    except toml.TomlDecodeError as e:
        raise HTTPException(status_code=400, detail=f"无效的 TOML 格式: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/config")
async def delete_config(filename: str):
    """删除指定配置文件"""
    filepath = os.path.join(CONFIG_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="文件不存在")
    
    # 检查是否正在运行
    if filename == active_config and frpc_process and frpc_process.poll() is None:
         raise HTTPException(status_code=400, detail="无法删除当前正在运行的配置文件")

    try:
        os.remove(filepath)
        return {"status": "success", "message": f"配置文件 {filename} 已删除"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/run")
async def run_config(data: RunConfig):
    """切换并运行指定的配置文件"""
    global active_config
    filepath = os.path.join(CONFIG_DIR, data.filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="文件不存在")
    
    active_config = data.filename
    start_frpc()
    return {"status": "success", "message": f"已切换到 {active_config} 并重启 frpc"}

@app.post("/api/stop")
async def stop_service():
    """停止 frpc 服务"""
    stop_frpc()
    return {"status": "success", "message": "frpc 已停止"}

@app.get("/api/status")
async def get_status():
    global frpc_process, active_config
    if frpc_process:
        return_code = frpc_process.poll()
        if return_code is None:
            return {"status": "running", "pid": frpc_process.pid, "active_config": active_config}
        else:
            # 如果崩溃了，读取 stderr
            stderr = frpc_process.stderr.read() if frpc_process.stderr else ""
            return {"status": "stopped", "exit_code": return_code, "error": stderr, "active_config": active_config}
    return {"status": "stopped", "active_config": active_config}



@app.get("/")
async def read_index():
    return FileResponse(os.path.join(STATIC_DIR, 'index.html'))

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/assets", StaticFiles(directory=os.path.join(STATIC_DIR, "assets")), name="assets")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
