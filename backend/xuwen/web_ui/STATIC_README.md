# xuwen/web_ui/static/ 是构建产物

`static/` 下的所有文件由 `backend/web_ui_src/` 通过 `npm run build` 自动生成。

**不要手动编辑 static/ 里的文件**。修改请走源码：

```bash
cd backend/web_ui_src
npm install
# 改 src/ 下的内容
npm run build    # 重新生成 ../xuwen/web_ui/static/（会清空目录再重建）
```

构建产物随 git 仓库一起提交，这样 git clone 后无需 Node.js 环境即可使用配置 UI。

## 后端访问

由 `xuwen/web_ui/app.py` 中的 `StaticFiles` mount 提供服务。
浏览器访问 `http://127.0.0.1:8000/config/` 或独立模式 `http://127.0.0.1:8765/config/`。
