# v6-beta 内网测试部署

这套部署使用独立的 `edullm-v6-beta` Compose 项目和数据库卷。原有 v6、v9 的容器和数据卷不参与升级。前端通过同源 `/api/` 代理访问后端；数据库和后端没有主机端口。默认只绑定 `127.0.0.1:18080`，须由经授权的内网 HTTPS 入口接入。

## 首次部署

1. 将 `.env.beta.example` 复制到仓库外的私有目录（例如 `/tmp/polytech-v6-beta-deploy/.env`），设文件权限为 `0600`，为数据库、JWT、管理员和教师分别生成随机且不同的密码。数据库密码使用十六进制字符串，因为它也被放入数据库 URL。不要使用 `backend.seed`：它会创建公开的演示密码和固定邀请码。
2. 将 `BETA_MODEL_NETWORK` 设为模型容器可访问的 Docker 网络，并设置模型 URL、名称和密钥。
3. 运行：

   ```sh
   docker compose -f docker-compose.beta.yml --env-file /path/to/private/.env up -d --build
   docker compose -f docker-compose.beta.yml --env-file /path/to/private/.env --profile bootstrap run --rm bootstrap
   ```

   `bootstrap` 只允许空用户表；重复执行会拒绝覆盖已有账号。生成的管理员与教师密码只保存在私有环境文件中。
4. 用经授权的内网 HTTPS 代理把浏览器流量转发到 `127.0.0.1:18080`。若要更改监听地址，调整 `BETA_BIND_IP` 与 `BETA_HTTP_PORT` 后重建前端容器。不要把数据库或后端端口发布到主机。
5. 用管理员和教师账号登录，创建真实课程与实验课，给受邀学生发课程邀请码。学生可自行注册；注册账号默认只有学生权限。

## 验证与备份

- 前端：`curl -I http://127.0.0.1:18080/`
- 后端：`curl http://127.0.0.1:18080/api/health`
- 容器：`docker compose -f docker-compose.beta.yml --env-file /path/to/private/.env ps`
- `backup` 容器启动后立即备份，并每 24 小时写一个 PostgreSQL 自定义格式归档到独立的 `edullm-v6-beta_beta_backups` 数据卷，保留约 14 天。单机备份不能防止整机故障；正式扩大测试前应复制到另一台机器或机构备份系统。
- 检查备份：`docker compose -f docker-compose.beta.yml --env-file /path/to/private/.env exec backup sh -lc 'for file in /backups/*.dump; do pg_restore -l "$file" >/dev/null || exit 1; done'`

当前机器的私有配置位于 `/tmp/polytech-v6-beta-deploy/.env`（`0600`）；另外有一份部署时的手工归档位于 `/tmp/polytech-v6-beta-deploy/backups/`。这些路径不属于 Git，换机器时需要单独安全迁移。
