# v6-beta 局域网内测部署

这套部署使用独立的 `edullm-v6-beta` Compose 项目和数据库卷。原有 v6、v9 的容器和数据卷不参与升级。前端通过同源 `/api/` 代理访问后端；数据库和后端没有主机端口。浏览器入口仅绑定指定的局域网 IP，另有一个只供本机诊断的 HTTP 端口。

## 当前机器

- 局域网地址：`https://192.168.168.199:18443/`（仅同一局域网可达）。
- 本机诊断地址：`http://127.0.0.1:18080/`。
- 私有配置：`/home/spark-1/.local/share/polytech-v6-beta/.env`，权限 `0600`。`/tmp/polytech-v6-beta-deploy/.env` 是指向它的链接。
- HTTPS 使用本机签发的 CA。将公开证书 `/home/spark-1/.local/share/polytech-v6-beta/tls/ca.crt` 安全地交给测试者，在其设备上导入受信任的 CA 后再访问。**只分发 `ca.crt`，不要分发任何 `.key` 文件。**导入前核对 CA 的 SHA-256 指纹：`CF:98:5F:46:7D:6A:66:59:74:8F:84:93:E6:9A:06:A5:BC:C6:7B:62:9E:CE:D7:02:25:DE:24:B3:37:B7:36:9D`。证书只覆盖 IP `192.168.168.199`，有效期至 2027-09-25；IP 改变时必须重新签发。
- 账号：私有配置中的 `BETA_ADMIN_USERNAME` / `BETA_ADMIN_PASSWORD` 与 `BETA_TEACHER_USERNAME` / `BETA_TEACHER_PASSWORD`。不要把管理员密码发给所有测试者。

## 首次部署或迁移

1. 将 `.env.beta.example` 复制到仓库外的持久私有目录，设权限为 `0600`；为数据库、JWT、管理员和教师分别生成随机且不同的密码。数据库密码使用十六进制字符串，因为它也被放入数据库 URL。不要运行 `backend.seed`：它会创建公开的演示密码和固定邀请码。
2. 将 `BETA_MODEL_NETWORK` 设为模型容器可访问的 Docker 网络，并设置模型 URL、名称和密钥。
3. 在 `BETA_TLS_DIR` 中准备 `server.crt` 和 `server.key`（私钥权限 `0600`）。证书的 Subject Alternative Name 必须包含 `BETA_LAN_IP`。优先使用机构认可的证书；使用本地 CA 时，向测试者单独分发并核对 CA 证书指纹。
4. 运行：

   ```sh
   docker compose -f docker-compose.beta.yml --env-file /path/to/private/.env up -d --build
   docker compose -f docker-compose.beta.yml --env-file /path/to/private/.env --profile bootstrap run --rm bootstrap
   ```

   `bootstrap` 只允许空用户表；重复执行会拒绝覆盖已有账号。
5. 用管理员和教师账号登录，创建真实课程与实验课，给受邀学生发课程邀请码。学生可自行注册；注册账号默认只有学生权限。

## 验证与备份

- 前端：`curl --cacert /path/to/private/tls/ca.crt -I https://192.168.168.199:18443/`
- 后端：`curl --cacert /path/to/private/tls/ca.crt https://192.168.168.199:18443/api/health`
- 容器：`docker compose -f docker-compose.beta.yml --env-file /path/to/private/.env ps`
- `backup` 容器启动后立即备份，并每 24 小时写一个 PostgreSQL 自定义格式归档到独立的 `edullm-v6-beta_beta_backups` 数据卷，保留约 14 天。单机备份不能防止整机故障；扩大测试前应复制到另一台机器或机构备份系统。
- 检查备份：`docker compose -f docker-compose.beta.yml --env-file /path/to/private/.env exec backup sh -lc 'for file in /backups/*.dump; do pg_restore -l "$file" >/dev/null || exit 1; done'`

另有一份部署时的手工归档位于 `/tmp/polytech-v6-beta-deploy/backups/`，仅供短期恢复。局域网以外的用户不能访问此入口；若某些校园 Wi-Fi 开启了客户端隔离，还需网络管理员放行相应流量。
