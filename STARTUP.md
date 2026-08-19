# CrispCode 一键启动说明

## 启动脚本

项目提供了多个一键启动脚本，可以根据需要选择使用：

### 1. Python 脚本（推荐）

#### `start.py` - 原始版本
```bash
./start.py
```

#### `start-crisp.py` - 简化版本
```bash
./start-crisp.py
```

### 2. Shell 脚本

#### `start.sh` - 原始版本
```bash
./start.sh
```

#### `start-crisp.sh` - 简化版本（使用现有命令）
```bash
./start-crisp.sh
```

## 工作原理

所有启动脚本都遵循以下流程：

1. **启动 crisp-core 服务器** - 在后台启动核心服务器
2. **等待服务器就绪** - 检测端口是否可连接
3. **启动 crisp-tui 客户端** - 启动终端用户界面
4. **优雅关闭** - 当 TUI 退出时，自动停止服务器

## 配置选项

启动脚本支持以下环境变量：

- `CRISP_HOST` - 服务器主机地址（默认：`127.0.0.1`）
- `CRISP_PORT` - 服务器端口（默认：`7437`）

示例：
```bash
# 使用自定义端口启动
CRISP_PORT=8080 ./start-crisp.sh

# 使用自定义主机和端口启动
CRISP_HOST=0.0.0.0 CRISP_PORT=8080 ./start-crisp.sh
```

## 手动启动

如果不想使用一键启动脚本，也可以手动启动：

### 1. 启动服务器
```bash
# 使用 crisp 命令启动（推荐）
crisp core start

# 或者直接运行
crisp-core
```

### 2. 启动客户端
```bash
crisp-tui
```

### 3. 停止服务器
```bash
# 使用 crisp 命令停止（推荐）
crisp core stop

# 或者找到进程 ID 并停止
ps aux | grep crisp-core
kill <PID>
```

## 故障排除

### 端口被占用
如果遇到端口被占用的问题，可以：
1. 停止现有的服务器：`crisp core stop`
2. 使用不同的端口：`CRISP_PORT=8080 ./start-crisp.sh`

### 权限问题
如果遇到权限问题：
```bash
chmod +x *.sh *.py
```

### 命令未找到
如果遇到命令未找到的问题，请确保已安装项目：
```bash
pip install -e .
```

## 开发模式

对于开发，可以使用以下方式：

```bash
# 启动服务器（显示日志）
crisp-core

# 在另一个终端启动客户端
crisp-tui
```

## 系统要求

- Python 3.12+
- 已安装的 CrispCode 项目
- 支持的操作系统：Linux、macOS、Windows（需要 WSL 或 Git Bash）