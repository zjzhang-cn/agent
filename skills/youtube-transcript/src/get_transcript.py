#!/usr/bin/env python3
"""
获取YouTube视频字幕的脚本
"""
import re
import sys
import json
import os
from urllib.parse import urlparse, parse_qs
from pathlib import Path

try:
    from youtube_transcript_api import YouTubeTranscriptApi
except ImportError:
    print("Error: youtube-transcript-api 未安装")
    print("请运行: pip install youtube-transcript-api")
    sys.exit(1)


def extract_video_id(url_or_id: str) -> str:
    """
    从URL或直接ID中提取YouTube视频ID
    """
    # 如果已经是纯ID（11位字母数字）
    if re.match(r'^[a-zA-Z0-9_-]{11}$', url_or_id):
        return url_or_id
    
    # 尝试解析各种YouTube URL格式
    patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})',
        r'youtube\.com/shorts/([a-zA-Z0-9_-]{11})',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url_or_id)
        if match:
            return match.group(1)
    
    return None


def get_transcript(video_id: str, language: str = None, languages: list = None) -> dict:
    """
    获取视频字幕
    """
    try:
        ytt_api = YouTubeTranscriptApi()
        
        # 确定语言列表
        if languages:
            lang_list = languages
        elif language:
            lang_list = [language]
        else:
            lang_list = ['en', 'zh-CN', 'zh-Hans', 'zh-TW', 'zh-Hant']
        
        # 获取字幕
        transcript_list = ytt_api.list(video_id)
        
        # 尝试找到可用的翻译
        try:
            transcript = transcript_list.find_transcript(lang_list)
        except Exception:
            # 如果找不到翻译字幕，尝试获取任何可用的
            available = list(transcript_list)
            if available:
                transcript = available[0].fetch()
                # 直接返回已有字幕的 fetch 结果
                return format_result(video_id, transcript)
            else:
                raise Exception("该视频没有可用的字幕")
        
        # 获取翻译后的字幕
        fetched_transcript = transcript.fetch()
        
        return format_result(video_id, fetched_transcript)
        
    except Exception as e:
        return {
            "success": False,
            "video_id": video_id,
            "error": str(e)
        }


def format_result(video_id: str, transcript) -> dict:
    """
    格式化字幕结果
    """
    # 转换为列表格式
    transcript_data = []
    full_text = ""
    
    for item in transcript:
        if hasattr(item, 'start'):
            # 新版本格式
            transcript_data.append({
                'start': item.start,
                'duration': item.duration,
                'text': item.text
            })
            full_text += item.text + " "
        elif isinstance(item, dict):
            # 字典格式
            transcript_data.append(item)
            full_text += item.get('text', '') + " "
    
    return {
        "success": True,
        "video_id": video_id,
        "transcript": transcript_data,
        "full_text": full_text.strip()
    }


def save_to_file(result: dict, output_dir: str = None) -> dict:
    """
    保存字幕到文件
    """
    if not result.get("success"):
        return {"saved": False, "error": "字幕获取失败，无法保存"}
    
    video_id = result.get("video_id")
    
    # 默认保存到当前目录的 transcripts 文件夹
    if output_dir is None:
        output_dir = "transcripts"
    
    # 创建输出目录
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    saved_files = []
    
    # 保存JSON格式
    json_file = output_path / f"{video_id}_transcript.json"
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    saved_files.append(str(json_file))
    
    #保存纯文本格式
    txt_file = output_path / f"{video_id}_transcript.txt"
    with open(txt_file, 'w', encoding='utf-8') as f:
        f.write(result.get('full_text', ''))
    saved_files.append(str(txt_file))
    
    return {
        "saved": True,
        "files": saved_files,
        "video_id": video_id,
        "transcript_count": len(result.get('transcript', [])),
        "char_count": len(result.get('full_text', ''))
    }


def main():
    # 获取命令行参数
    args = sys.argv[1:]
    
    # 解析参数
    save_files = False
    output_dir = None
    url_or_id = None
    language = None
    
    i = 0
    while i < len(args):
        if args[i] == '--save' or args[i] == '-s':
            save_files = True
        elif args[i] == '--output' or args[i] == '-o':
            i += 1
            if i < len(args):
                output_dir = args[i]
        elif args[i] in ['--help', '-h']:
            print("Usage: python get_transcript.py [options] <youtube_url or video_id> [language]")
            print("\nOptions:")
            print("  -s, --save          保存字幕到文件")
            print("  -o, --output DIR    指定输出目录（默认: transcripts）")
            print("  -h, --help          显示帮助信息")
            print("\nExamples:")
            print("  python get_transcript.py https://www.youtube.com/watch?v=dQw4w9WgXcQ")
            print("  python get_transcript.py -s dQw4w9WgXcQ")
            print("  python get_transcript.py -s -o my_subtitles https://www.youtube.com/watch?v=abc123 en")
            sys.exit(0)
        else:
            # 第一个非选项参数视为视频URL或ID
            if url_or_id is None:
                url_or_id = args[i]
            elif language is None:
                language = args[i]
        i += 1
    
    if not url_or_id:
        print("Error: 请提供YouTube视频URL或视频ID")
        print("Usage: python get_transcript.py <youtube_url or video_id> [language]")
        sys.exit(1)
    
    # 提取视频ID
    video_id = extract_video_id(url_or_id)
    
    if not video_id:
        print(json.dumps({
            "success": False,
            "error": "无效的YouTube链接或视频ID"
        }))
        sys.exit(1)
    
    # 获取字幕
    result = get_transcript(video_id, language)
    
    # 如果需要保存到文件
    if save_files:
        save_result = save_to_file(result, output_dir)
        # 在JSON结果中添加保存信息
        result['save_info'] = save_result
        print(json.dumps(save_result, ensure_ascii=False, indent=2))
    else:
        # 输出JSON结果
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()