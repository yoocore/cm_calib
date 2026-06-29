#!/usr/bin/env python3
"""生成标定工具HTML使用说明文档"""

import base64
from pathlib import Path

def get_base64_image(image_path):
    """将图片转换为base64字符串"""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode('utf-8')

def generate_html():
    """生成包含图片的HTML文档"""
    pics_dir = Path("D:/Desktop/CalibTool_pics")

    # 定义文档结构
    sections = [
        {
            "title": "工具介绍",
            "type": "intro",
            "features": [
                "支持多种标定板类型和相机型号",
                "集成多路径寻优、坐标下降、贝叶斯优化等算法",
                "实时显示标定进度和得分变化",
                "提供虚实重叠验证，直观评估标定效果",
                "自动记录历史数据，支持多次标定对比"
            ]
        },
        {
            "title": "第一部分：界面概览",
            "items": [
                ("初始页面", "软件启动界面，整体布局介绍"),
                ("cm配置区", "配置参数区域，包括相机参数、标定板规格等"),
                ("标定设置区", "标定算法和参数设置"),
                ("输出区", "标定结果和日志输出显示"),
                ("进度区", "标定进度实时显示"),
            ]
        },
        {
            "title": "第二部分：标定流程",
            "items": [
                ("标定开始", "启动标定过程的界面"),
                ("标定进行", "标定执行中的状态展示"),
            ]
        },
        {
            "title": "第三部分：评分与验证",
            "items": [
                ("本轮得分趋势", "当前轮次标定效果的得分变化"),
                ("历史得分趋势", "全部标定轮次的得分历史统计"),
                ("得分", "标定得分汇总和指标展示"),
                ("虚实重叠", "实际图像与虚拟图像的对比叠加，用于验证标定效果"),
                ("标定完成", "标定流程完成后的最终结果界面"),
            ]
        }
    ]

    html_content = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>标定工具使用说明</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans SC', sans-serif;
            line-height: 1.6;
            color: #2c3e50;
            background: linear-gradient(135deg, #f5f7fa 0%, #e4e8ec 100%);
            min-height: 100vh;
            padding: 40px 20px;
        }

        .container {
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            border-radius: 16px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.1);
            overflow: hidden;
        }

        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 40px 50px;
            text-align: center;
        }

        .header h1 {
            font-size: 2.5em;
            margin-bottom: 10px;
            font-weight: 700;
        }

        .header p {
            font-size: 1.1em;
            opacity: 0.9;
        }

        .intro-section {
            background: linear-gradient(135deg, #667eea22 0%, #764ba222 100%);
            padding: 40px 50px;
            border-bottom: 2px solid #e9ecef;
        }

        .intro-features ul {
            list-style: none;
            display: flex;
            flex-wrap: nowrap;
            gap: 12px;
        }

        .intro-features li {
            flex: 0 0 auto;
            background: white;
            padding: 12px 20px;
            border-radius: 8px;
            border-left: 4px solid #667eea;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
            white-space: nowrap;
        }

        .toc {
            background: #f8f9fa;
            padding: 30px 50px;
            border-bottom: 2px solid #e9ecef;
        }

        .toc h2 {
            color: #667eea;
            margin-bottom: 15px;
            font-size: 1.3em;
        }

        .toc ul {
            list-style: none;
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 8px;
        }

        .toc-section {
            margin-bottom: 15px;
        }

        .toc-section-title {
            color: #667eea;
            font-weight: 600;
            font-size: 1.05em;
            margin-bottom: 8px;
            padding-left: 8px;
            border-left: 3px solid #667eea;
        }

        .toc-section ul {
            list-style: none;
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
        }

        .toc-section li {
            flex: 0 0 auto;
        }

        .toc a {
            color: #495057;
            text-decoration: none;
            padding: 8px 12px;
            display: block;
            border-radius: 6px;
            transition: all 0.2s;
        }

        .toc a:hover {
            background: #667eea;
            color: white;
        }

        .section {
            padding: 50px;
            border-bottom: 1px solid #e9ecef;
        }

        .section:last-child {
            border-bottom: none;
        }

        .section h2 {
            color: #667eea;
            font-size: 1.8em;
            margin-bottom: 30px;
            padding-bottom: 15px;
            border-bottom: 3px solid #667eea;
            display: inline-block;
        }

        .item {
            display: flex;
            gap: 40px;
            margin-bottom: 50px;
            align-items: flex-start;
        }

        .item:nth-child(even) {
            flex-direction: row-reverse;
        }

        .item-content {
            flex: 1;
            padding: 25px;
            background: #f8f9fa;
            border-radius: 12px;
        }

        .item-content h3 {
            color: #495057;
            font-size: 1.4em;
            margin-bottom: 12px;
        }

        .item-content p {
            color: #6c757d;
            font-size: 1.05em;
        }

        .item-image {
            flex: 1;
            max-width: 600px;
        }

        .item-image img {
            width: 100%;
            height: auto;
            border-radius: 12px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.15);
            transition: transform 0.3s;
        }

        .item-image img:hover {
            transform: scale(1.02);
        }

        .flowchart {
            background: linear-gradient(135deg, #667eea22 0%, #764ba222 100%);
            padding: 40px;
            border-radius: 16px;
            margin: 50px 0;
        }

        .flowchart h2 {
            color: #667eea;
            margin-bottom: 25px;
            text-align: center;
        }

        .flow-steps {
            display: flex;
            justify-content: center;
            flex-wrap: wrap;
            gap: 15px;
        }

        .flow-step {
            background: white;
            padding: 15px 25px;
            border-radius: 50px;
            font-weight: 600;
            color: #667eea;
            box-shadow: 0 5px 20px rgba(102,126,234,0.2);
        }

        .flow-arrow {
            display: flex;
            align-items: center;
            color: #667eea;
            font-size: 1.5em;
        }

        .footer {
            background: #f8f9fa;
            padding: 30px 50px;
            text-align: center;
            color: #6c757d;
            font-size: 0.9em;
        }

        @media (max-width: 900px) {
            .item {
                flex-direction: column !important;
            }
            .item-image {
                max-width: 100%;
            }
        }

        /* 图片双击放大 modal 样式 */
        .image-modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.9);
            z-index: 9999;
            justify-content: center;
            align-items: center;
            cursor: zoom-out;
        }

        .image-modal.active {
            display: flex;
        }

        .image-modal img {
            max-width: 90vw;
            max-height: 90vh;
            object-fit: contain;
            border-radius: 8px;
            box-shadow: 0 10px 40px rgba(0, 0, 0, 0.5);
        }

        .image-modal .close-btn {
            position: absolute;
            top: 20px;
            right: 30px;
            color: white;
            font-size: 40px;
            font-weight: bold;
            cursor: pointer;
            z-index: 10000;
            transition: color 0.2s;
        }

        .image-modal .close-btn:hover {
            color: #667eea;
        }

        /* 图片悬停提示 */
        .item-image img {
            cursor: zoom-in;
        }
    </style>
    <script>
        // 图片双击放大功能
        document.addEventListener('DOMContentLoaded', function() {
            // 创建 modal 元素
            const modal = document.createElement('div');
            modal.className = 'image-modal';
            modal.innerHTML = '<span class="close-btn">&times;</span><img src="" alt="放大图片">';
            document.body.appendChild(modal);

            const modalImg = modal.querySelector('img');
            const closeBtn = modal.querySelector('.close-btn');

            // 为所有图片添加双击事件
            document.querySelectorAll('.item-image img').forEach(img => {
                img.addEventListener('dblclick', function(e) {
                    e.preventDefault();
                    modalImg.src = this.src;
                    modalImg.alt = this.alt;
                    modal.classList.add('active');
                });
            });

            // 点击关闭按钮关闭 modal
            closeBtn.addEventListener('click', function() {
                modal.classList.remove('active');
            });

            // 点击 modal 背景关闭
            modal.addEventListener('click', function(e) {
                if (e.target === modal) {
                    modal.classList.remove('active');
                }
            });

            // ESC 键关闭 modal
            document.addEventListener('keydown', function(e) {
                if (e.key === 'Escape' && modal.classList.contains('active')) {
                    modal.classList.remove('active');
                }
            });
        });
    </script>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📸 标定工具使用说明</h1>
            <p>完整的界面介绍与操作流程指南</p>
        </div>
"""

    # 生成工具介绍部分（在导航之前）
    for section in sections:
        if section.get("type") == "intro":
            html_content += f"""
        <div class="section intro-section">
            <h2>{section['title']}</h2>
            <div class="intro-features">
                <ul>
"""
            for feature in section["features"]:
                html_content += f'                    <li>{feature}</li>\n'
            html_content += """
                </ul>
            </div>
        </div>
"""

    html_content += """
        <div class="toc">
            <h2>📑 目录导航</h2>
"""

    # 生成目录（跳过intro类型）
    for section in sections:
        if section.get("type") != "intro":
            html_content += f'            <div class="toc-section">\n'
            html_content += f'                <div class="toc-section-title">{section["title"]}</div>\n'
            html_content += f'                <ul>\n'
            for item_name, _ in section["items"]:
                html_content += f'                    <li><a href="#{item_name}">{item_name}</a></li>\n'
            html_content += f'                </ul>\n'
            html_content += f'            </div>\n'

    html_content += """
        </div>
"""

    # 生成各部分内容（跳过intro类型）
    for section in sections:
        if section.get("type") != "intro":
            html_content += f"""
        <div class="section">
            <h2>{section['title']}</h2>
"""

            for i, (item_name, item_desc) in enumerate(section["items"]):
                image_path = pics_dir / f"{item_name}.png"
                if image_path.exists():
                    img_base64 = get_base64_image(image_path)
                    html_content += f"""
            <div class="item" id="{item_name}">
                <div class="item-content">
                    <h3>{item_name}</h3>
                    <p>{item_desc}</p>
                </div>
                <div class="item-image">
                    <img src="data:image/png;base64,{img_base64}" alt="{item_name}">
                </div>
            </div>
"""

            html_content += "        </div>\n"

    # 生成流程图
    html_content += """
        <div class="flowchart">
            <h2>🔄 标定流程概览</h2>
            <div class="flow-steps">
                <div class="flow-step">初始页面</div>
                <div class="flow-arrow">→</div>
                <div class="flow-step">cm配置区</div>
                <div class="flow-arrow">→</div>
                <div class="flow-step">标定设置区</div>
                <div class="flow-arrow">→</div>
                <div class="flow-step">标定开始</div>
                <div class="flow-arrow">→</div>
                <div class="flow-step">标定进行</div>
                <div class="flow-arrow">→</div>
                <div class="flow-step">实时得分监控</div>
                <div class="flow-arrow">→</div>
                <div class="flow-step">虚实重叠验证</div>
                <div class="flow-arrow">→</div>
                <div class="flow-step">标定完成</div>
            </div>
        </div>

        <div class="footer">
            <p>📅 文档生成时间：2026-06-29 | 标定工具使用说明 v1.0</p>
        </div>
    </div>
</body>
</html>
"""

    # 写入文件
    output_file = Path("标定工具使用说明.html")
    output_file.write_text(html_content, encoding='utf-8')
    print(f"[OK] HTML 文档已生成：{output_file.absolute()}")

if __name__ == "__main__":
    generate_html()
