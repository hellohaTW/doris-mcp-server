<!--
Licensed to the Apache Software Foundation (ASF) under one
or more contributor license agreements.  See the NOTICE file
distributed with this work for additional information
regarding copyright ownership.  The ASF licenses this file
to you under the Apache License, Version 2.0 (the
"License"); you may not use this file except in compliance
with the License.  You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing,
software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.  See the License for the
specific language governing permissions and limitations
under the License.
-->

# 本地 Docker 部署

[English](README.md) | [简体中文](README.zh-CN.md)

本目录的 `docker-compose.yml` **只启动 Doris MCP Server 一个容器**，使用
Streamable HTTP，并且只发布在回环地址上，适合开发机使用。仓库根目录的
`docker-compose.yml` 是另一回事：那是包含 Doris、Redis、Prometheus、Grafana、
Nginx 的完整技术栈。

容器内部绑定 `0.0.0.0`，Server 将其视为非回环绑定，会拒绝以未认证方式启动。
因此本文件启用了静态 Token 认证，下面第 2 步不可跳过。

## 前置条件

- Docker Engine 20.10+ 且带 Compose v2 插件（`docker compose version`）；
- 一个可访问的 Apache Doris 2.0.0+ FE MySQL 端点，或使用文末的可选 `doris` profile；
- 一个给 Server 使用的 Doris 账号。只读权限即可 —— 可见对象与数据最终仍由
  Doris RBAC 决定。

## 1. 配置 Doris 连接

```bash
cd deploy/local
cp .env.example .env
```

编辑 `.env`，设置 `DORIS_HOST`、`DORIS_PORT`、`DORIS_USER`、`DORIS_DATABASE`。
如果 Doris 跑在你自己的机器上，保持 `DORIS_HOST=host.docker.internal`：容器里的
`localhost` 指的是容器自身，不是宿主机。

## 2. 创建两个 secret 文件

密钥以文件形式提供，而不是环境变量。两个文件都必须只包含一行非空内容，
空文件或多行内容会被 Server 拒绝。

```bash
# 上面那个 Doris 账号的密码
printf '%s' 'your-doris-password' > secrets/doris_password

# MCP Host 要发送的 bearer token：至少 32 个字符、至少 10 个不同字符，
# 明显像占位符的值会被拒绝。
python3 -c 'import secrets; print(secrets.token_urlsafe(32), end="")' > secrets/mcp_static_token

chmod 644 secrets/doris_password secrets/mcp_static_token
```

这里用 `644` 是有意为之。Compose 会带着宿主机的属主和权限位把这两个文件挂载进
容器，而镜像以非 root 的 `doris` 账号运行；如果文件是属于你自己的 `600`，容器内
读不到，Server 会以 `is not a readable file` 退出。只有在同时把文件属主改成容器
内的 uid（`docker compose exec doris-mcp-server id -u`）时才保留 `600`；也请注意
`644` 意味着本机所有用户都能读到你的 Doris 密码。

`secrets/.gitignore` 会阻止这两个文件被提交。之后要再看 token：
`cat secrets/mcp_static_token`。

## 3. 构建并启动

```bash
docker compose up -d --build
```

首次构建需要几分钟。之后只有在服务端源码或 `requirements.txt` 变更时才需要
再加 `--build`。

```bash
docker compose ps          # STATUS 应变为 "healthy"
docker compose logs -f     # 跟踪日志；Ctrl+C 只是停止跟踪，不会停容器
```

这里的 `healthy` 表示基于 Doris 的就绪探针通过了，所以容器健康同时也证明
Doris 连接是通的。

Server 是用「stdout 是不是 tty」来决定要不要输出到 console 的，并且把非 tty 的
stdout 当成 stdio 传输模式（该模式下 console 输出会破坏 MCP 帧）。因此
`docker compose logs` 默认会是空的，本文件用 `tty: true` 解决。同一份日志也会写
在容器内的 `/app/logs`：`docker compose exec doris-mcp-server ls /app/logs`。

## 4. 验证

```bash
TOKEN="$(cat secrets/mcp_static_token)"

# 进程存活检查，无需认证
curl --fail http://127.0.0.1:3000/live

# 基于 Doris 的就绪检查
curl --fail http://127.0.0.1:3000/ready

# 一次带认证的 MCP initialize 往返
curl -sS http://127.0.0.1:3000/mcp \
  -H "Authorization: Bearer ${TOKEN}" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
          "protocolVersion": "2026-07-28",
          "capabilities": {},
          "clientInfo": {"name": "curl", "version": "0"}
        }
      }'
```

返回带服务器名称的 JSON-RPC result，说明容器、认证边界、协议层都正常。

## 5. 接入 MCP Host

端点为 `http://127.0.0.1:3000/mcp`，请求头
`Authorization: Bearer <secrets/mcp_static_token 的内容>`。对于使用 JSON 配置的
Host：

```json
{
  "mcpServers": {
    "doris": {
      "type": "http",
      "url": "http://127.0.0.1:3000/mcp",
      "headers": {
        "Authorization": "Bearer REPLACE_WITH_TOKEN"
      }
    }
  }
}
```

只支持早期握手版 Streamable HTTP 的 Host（如 Dify 1.16.1，MCP `2025-06-18`）
需要在 `.env` 中设置 `ENABLE_LEGACY_HTTP_ADAPTER=true`，并使用
`http://127.0.0.1:3000/mcp/legacy`。参见
[Host 集成](../../docs/integrations/hosts.zh-CN.md)。

## 常用命令

```bash
docker compose ps                     # 查看状态
docker compose logs -f doris-mcp-server
docker compose restart doris-mcp-server   # 修改 .env 后
docker compose up -d --build          # 修改服务端源码后
docker compose down                   # 停止并删除容器
docker compose down -v                # 同时删除可选 Doris 的数据卷
```

修改 `.env` 后必须执行 `docker compose up -d`（或 `restart`），运行中的容器不会
自动读取新值。

## 可选：在同一份文件里起一个临时 Doris

如果你手上完全没有 Doris，`doris` profile 会在 Server 旁边启动单 FE、单 BE 的
集群，仅供本地实验使用。

```bash
# Linux 宿主机在启动 Doris BE 前需要执行，否则 BE 起不来
sudo sysctl -w vm.max_map_count=2000000

docker compose --profile doris up -d --build
```

然后在 `.env` 中设置并重启 Server：

```bash
DORIS_HOST=doris-fe
DORIS_USER=root
```

FE 和 BE 都会把同一个 `secrets/doris_password` 挂载到 `/etc/basic_auth/password`，
这与仓库根目录 `docker-compose.yml` 对这两个镜像的用法一致。启动后请等待一分钟：
BE 需要先向 FE 注册，就绪状态才会变绿。如果此时 `/ready` 仍因认证失败，请自行在
集群上设置密码，然后重启 Server：

```bash
mysql -h 127.0.0.1 -P 9030 -u root
# ALTER USER 'root'@'%' IDENTIFIED BY '<secrets/doris_password 的内容>';
```

`docker compose down -v` 会删除 Doris 数据卷及其中所有数据。

## 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 容器立即退出，日志提示拒绝未认证的非回环绑定 | `secrets/mcp_static_token` 缺失或强度不足 | 按第 2 步重新生成 |
| `TOKEN_ADMIN_FILE is not a readable file` | secret 文件不存在，或容器内非 root 用户读不到 | 创建文件并 `chmod 644` |
| `must contain one non-empty line` | secret 文件为空或有多行 | 用 `printf '%s'` 重写，不要换行 |
| `/ready` 失败但 `/live` 正常 | Doris 连接配置错误或不可达 | 检查 `DORIS_HOST`/端口；在宿主机执行 `mysql -h <host> -P 9030 -u <user> -p` |
| 连接宿主机上的 Doris 被拒绝 | 容器内用了 `localhost` | 改用 `host.docker.internal` |
| `/mcp` 返回 `401` | 缺少或写错 bearer token | 发送 `Authorization: Bearer $(cat secrets/mcp_static_token)` |
| 工具尚未执行就返回 `400`/`403` | Host 或 Origin 头不在允许列表内 | 把确切的地址加入 `MCP_ALLOWED_HOSTS` / `MCP_ALLOWED_ORIGINS` |
| 端口 3000 已被占用 | 其他进程占用该端口 | 在 `.env` 中设置 `MCP_HTTP_PORT` |

更多内容：[故障排查](../../docs/operations/troubleshooting.zh-CN.md)、
[部署](../../docs/operations/deployment.zh-CN.md)、
[配置参考](../../docs/reference/configuration.zh-CN.md)。

## 安全提示

本文件有意只监听回环地址。在对外暴露之前，请先阅读
[安全模型](../../docs/security/security-model.zh-CN.md)。至少做到：发布端口保持在
`127.0.0.1`；TLS 由反向代理终止而不是在这里；Doris 账号只授予必要的读权限；
不要提交 `secrets/` 和 `.env`。
