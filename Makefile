# CrispCode Makefile

.PHONY: help start start-python start-shell stop status clean

help: ## 显示帮助信息
	@echo "CrispCode 一键启动命令"
	@echo ""
	@echo "用法:"
	@echo "  make start          使用简化脚本启动 (推荐)"
	@echo "  make start-python   使用 Python 脚本启动"
	@echo "  make start-shell    使用 Shell 脚本启动"
	@echo "  make stop           停止服务器"
	@echo "  make status         查看服务器状态"
	@echo "  make clean          清理 PID 文件"
	@echo ""
	@echo "环境变量:"
	@echo "  CRISP_HOST          服务器主机 (默认: 127.0.0.1)"
	@echo "  CRISP_PORT          服务器端口 (默认: 7437)"

start: ## 使用简化脚本启动
	@./start-crisp.sh

start-python: ## 使用 Python 脚本启动
	@./start-crisp.py

start-shell: ## 使用 Shell 脚本启动
	@./start.sh

stop: ## 停止服务器
	@crisp core stop

status: ## 查看服务器状态
	@crisp core status

clean: ## 清理 PID 文件
	@rm -f ~/.crispcode/crisp-core.pid
	@echo "PID 文件已清理"

test: ## 测试启动脚本
	@./test-startup.sh

install: ## 安装启动命令到 PATH
	@mkdir -p ~/.local/bin
	@cp crisp-start ~/.local/bin/
	@echo "已安装 crisp-start 到 ~/.local/bin/"
	@echo "请确保 ~/.local/bin 在您的 PATH 中"

uninstall: ## 卸载启动命令
	@rm -f ~/.local/bin/crisp-start
	@echo "已卸载 crisp-start"