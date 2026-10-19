# Afterglow documentation · 文档

[简体中文](https://kldhsh123.github.io/Afterglow/zh/) | [English](https://kldhsh123.github.io/Afterglow/en/)

[Discord community / Discord 社区](https://discord.gg/CKrvgCyK4y)
· [QQ group / QQ 群：330316577](https://qm.qq.com/cgi-bin/qm/qr?k=7rvmrvR100Is9aAp0ZsjmfiG7e0Cv6ZB&jump_from=webapi&authKey=mEN/epjvPHcT+Sb61/XO0Mi5egs2xJBhZm9Wm5MmgEWrpNa0ZOY3fzUf+pvqfijD)

主要入口为 [GitHub Pages 文档站](https://kldhsh123.github.io/Afterglow)。中文源在 [`wiki/`](wiki/Home.md)，英文源在 [`en/`](en/index.md)，一种语言一套独立文件；网站导航文案按语言放在 `.vitepress/locales/`。旧 Wiki 是自动生成的兼容镜像，不直接编辑。

The primary reader interface is the bilingual [documentation website](https://kldhsh123.github.io/Afterglow). Maintain Chinese articles in [`wiki/`](wiki/Home.md), English articles in [`en/`](en/index.md), and site UI text in one module per language under `.vitepress/locales/`. Generated site output and the Wiki mirror must not be edited directly.

Local development / 本地开发（Python 3, Node.js 20+）：

```bash
cd docs
npm ci
npm run dev
```

Validation and production build / 校验及生产构建：

```bash
npm test
npm run build
npm run check:dist
npm run preview
```

Publishing instructions / 发布说明：[中文](wiki/文档维护.md) · [English](en/documentation.md)。
