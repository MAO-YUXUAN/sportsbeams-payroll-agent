# Sportsbeams Payroll Agent

赛倍明智能工资核算工作台，将每月工资核算中的 Excel 滚动更新、资料收集、考勤计算、第三方核对、付款申请和审计记录整合为一套本地工作流。

## 主要功能

- 自动复制上月工资 Sheet，更新月份标题及累计个税公式
- 识别亚润、易才、考勤、社保、个税和公积金文件
- 计算餐补、交通补贴、请假及缺勤扣款
- 核对第三方结算数据并生成亚润、易才工资表
- 使用默认模板生成第三方及内部付款申请单
- 发现新员工后，由 Agent 收集入职日期、部门和基础工资等信息
- 在关键写入动作前保留人工确认节点
- 保存输入、输出、审批与审计记录

## 技术栈

- 后端：Python、FastAPI、Microsoft Excel COM（pywin32）
- 前端：React、TypeScript、Vite
- 对话能力：DeepSeek API（可选）

## 环境要求

- Windows 10/11
- Python 3.11 或更高版本
- Microsoft Excel 桌面版
- Node.js 20 或更高版本

## 本地运行

安装后端依赖：

```powershell
python -m pip install -r requirements.txt
```

安装并构建前端：

```powershell
Set-Location frontend
npm install
npm run build
Set-Location ..
```

启动应用：

```powershell
.\launcher\start_app.ps1
```

也可以只启动后端：

```powershell
.\launcher\start_backend.ps1
```

默认访问地址为 <http://127.0.0.1:8765>。

## DeepSeek 配置

API Key 不写入代码或配置文件，而是保存在当前 Windows 用户的加密凭据存储中。运行以下命令配置：

```powershell
.\launcher\setup_llm_key.ps1
```

## 数据安全

`data/` 包含工资输入文件、生成结果、运行状态和审计日志，已被 Git 忽略。请勿将真实员工工资数据、身份证号、银行卡信息或 API Key 提交到版本库。

## 测试

```powershell
python -m pytest -q
```

