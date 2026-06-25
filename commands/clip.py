"""YouTube/Bilibili视频下载+转录+笔记子命令"""
import os
import re
import sys
import time
import random
import tempfile
import requests
import urllib3
import json
from openai import OpenAI
from datetime import datetime
from urllib.parse import urlparse, parse_qs

sys.stdout.reconfigure(encoding="utf-8")

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

COOKIES_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cookies.txt")

# B站全局限流追踪：记录最后一次412的时间，所有视频共享冷却
_bilibili_last_412 = 0.0

CHECKPOINT_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".clip_checkpoint.json")


def load_checkpoint():
    try:
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except:
        return set()


def save_checkpoint(video_id):
    done = load_checkpoint()
    done.add(video_id)
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(done), f, ensure_ascii=False)


BILIBILI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com",
    "Origin": "https://www.bilibili.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _get_config():
    from core.config import get_config
    return get_config()


def extract_video_id(url):
    pattern = r"(?:v=|youtu\.be/|youtube\.com/embed/|youtube\.com/watch\?v=)([a-zA-Z0-9_-]{11})"
    match = re.search(pattern, url)
    return match.group(1) if match else None


def is_youtube_url(url):
    return "youtube.com" in url or "youtu.be" in url


def is_bilibili_url(url):
    return "bilibili.com" in url or "b23.tv" in url


def extract_bilibili_video_id(url):
    # 标准格式: https://www.bilibili.com/video/BV1xx411c7mD
    bv_pattern = r"bilibili\.com/video/(BV[a-zA-Z0-9]+)"
    match = re.search(bv_pattern, url)
    if match:
        return match.group(1)
    
    # 短链接格式: https://b23.tv/xxxxx
    if "b23.tv" in url:
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"}, timeout=10, allow_redirects=True)
            final_url = r.url
            match = re.search(bv_pattern, final_url)
            if match:
                return match.group(1)
        except:
            pass
    
    return None


def fetch_bilibili_video_info(video_id):
    """获取B站视频详细信息（包含合集信息）"""
    try:
        info_url = f"https://api.bilibili.com/x/web-interface/view?bvid={video_id}"
        r = requests.get(info_url, headers=BILIBILI_HEADERS, timeout=10)
        data = r.json()
        if data["code"] == 0:
            return data["data"]
    except Exception:
        pass
    return None


def is_bilibili_collection(url):
    """检测是否为B站合集页面"""
    # 合集页面URL: bilibili.com/video/BVxxx?...&sid=xxx 或 bilibili.com/medialist/play/xxx
    if "season_id" in url or "medialist" in url:
        return True
    # 检查URL是否包含合集参数
    if "bilibili.com" in url and ("sid=" in url or "list=" in url):
        return True
    return False


def check_bilibili_collection_from_api(video_id):
    """通过API检测视频是否属于合集"""
    info = fetch_bilibili_video_info(video_id)
    if not info:
        return False, None
    
    ugc_season = info.get("ugc_season")
    if ugc_season and ugc_season.get("sections"):
        episodes = []
        for section in ugc_season.get("sections", []):
            for ep in section.get("episodes", []):
                if ep.get("bvid"):
                    episodes.append(ep)
        if len(episodes) > 1:
            return True, ugc_season.get("title", "")
    
    return False, None


def check_bilibili_multi_part(video_id):
    """检测是否为分P视频（单个BV号下多个视频）"""
    info = fetch_bilibili_video_info(video_id)
    if not info:
        return False, 0, None
    
    pages = info.get("pages", [])
    if len(pages) > 1:
        return True, len(pages), info.get("title", "")
    
    return False, 0, None


def fetch_bilibili_multi_part_text(video_id, page_num, cid):
    """获取分P视频的字幕或音频转录"""
    # 尝试获取字幕 - 多种方式
    print(f"  -> 尝试获取字幕 (cid={cid})...")
    
    # 方式1: 通过player API获取字幕列表
    try:
        subtitle_url = f"https://api.bilibili.com/x/player/v2?bvid={video_id}&cid={cid}"
        r = requests.get(subtitle_url, headers=BILIBILI_HEADERS, timeout=10)
        player_data = r.json()
        subtitles = player_data.get("data", {}).get("subtitle", {}).get("subtitles", [])
        
        print(f"  -> 找到 {len(subtitles)} 个字幕")
        
        if subtitles:
            subtitle_info = None
            for sub in subtitles:
                lang = sub.get("lan", "")
                print(f"     字幕: {lang} - {sub.get('lan_doc', '')}")
                if "zh" in lang:
                    subtitle_info = sub
                    break
            if not subtitle_info and subtitles:
                subtitle_info = subtitles[0]
            
            if subtitle_info:
                subtitle_file_url = subtitle_info["subtitle_url"]
                if subtitle_file_url.startswith("//"):
                    subtitle_file_url = "https:" + subtitle_file_url
                r = requests.get(subtitle_file_url, headers=BILIBILI_HEADERS, timeout=10)
                subtitle_data = r.json()
                text = "\n".join([item["content"] for item in subtitle_data.get("body", [])])
                if text:
                    print(f"  ✅ 获取字幕成功：{len(text)}字")
                    return text
    except Exception as e:
        print(f"  ⚠️ 字幕获取失败: {str(e)[:50]}")
    
    # 方式2: 尝试AI生成字幕
    try:
        ai_subtitle_url = f"https://api.bilibili.com/x/player/wbi/v2?bvid={video_id}&cid={cid}"
        r = requests.get(ai_subtitle_url, headers=BILIBILI_HEADERS, timeout=10)
        player_data = r.json()
        subtitles = player_data.get("data", {}).get("subtitle", {}).get("subtitles", [])
        
        if subtitles:
            for sub in subtitles:
                subtitle_file_url = sub.get("subtitle_url", "")
                if subtitle_file_url:
                    if subtitle_file_url.startswith("//"):
                        subtitle_file_url = "https:" + subtitle_file_url
                    r = requests.get(subtitle_file_url, headers=BILIBILI_HEADERS, timeout=10)
                    subtitle_data = r.json()
                    text = "\n".join([item["content"] for item in subtitle_data.get("body", [])])
                    if text:
                        print(f"  ✅ 获取AI字幕成功：{len(text)}字")
                        return text
    except Exception:
        pass
    
    print(f"  ⚠️ 无可用字幕，尝试音频转录...")
    
    # 无字幕，使用现有的音频下载和转录函数
    url = f"https://www.bilibili.com/video/{video_id}?p={page_num}"
    raw_text, _ = get_bilibili_text(url)
    return raw_text


def get_bilibili_collection_from_video(video_id):
    """从视频信息中检测是否属于合集，返回合集视频列表"""
    info = fetch_bilibili_video_info(video_id)
    if not info:
        return None, None
    
    # 检查是否属于UGC合集（ugc_season）
    ugc_season = info.get("ugc_season")
    if ugc_season:
        season_id = ugc_season.get("id")
        season_title = ugc_season.get("title", "")
        sections = ugc_season.get("sections", [])
        
        videos = []
        for section in sections:
            for ep in section.get("episodes", []):
                bvid = ep.get("bvid")
                title = ep.get("title", "")
                if bvid:
                    videos.append({"bvid": bvid, "title": title})
        
        if videos:
            return videos, season_title
    
    return None, None


def fetch_bilibili_collection(url):
    """获取B站合集中的所有视频BV号"""
    import re as _re
    
    # 尝试从URL提取season_id
    season_match = _re.search(r'season_id[=:](\d+)', url)
    sid_match = _re.search(r'sid[=:](\d+)', url)
    
    season_id = None
    if season_match:
        season_id = season_match.group(1)
    elif sid_match:
        season_id = sid_match.group(1)
    
    if not season_id:
        # 尝试从medialist URL提取
        ml_match = _re.search(r'medialist/play/(\d+)', url)
        if ml_match:
            season_id = ml_match.group(1)
    
    if not season_id:
        return []
    
    # 获取合集信息
    videos = []
    page = 1
    page_size = 30
    
    while True:
        api_url = f"https://api.bilibili.com/x/polymer/web-space/seasons_archives_list?mid=&season_id={season_id}&page={page}&page_size={page_size}"
        try:
            r = requests.get(api_url, headers=BILIBILI_HEADERS, timeout=10)
            data = r.json()
            
            if data.get("code") != 0:
                break
            
            archives = data.get("data", {}).get("archives", [])
            if not archives:
                break
            
            for item in archives:
                bvid = item.get("bvid", "")
                title = item.get("title", "")
                if bvid:
                    videos.append({"bvid": bvid, "title": title})
            
            # 检查是否有下一页
            total = data.get("data", {}).get("page", {}).get("total", 0)
            if page * page_size >= total:
                break
            page += 1
            
        except Exception as e:
            print(f"  ⚠️ 获取合集信息失败: {str(e)[:50]}")
            break
    
    return videos


def fetch_bilibili_subtitle(video_id):
    """获取B站视频字幕，优先使用CC字幕，其次使用AI生成字幕"""
    try:
        # 获取视频信息
        info_url = f"https://api.bilibili.com/x/web-interface/view?bvid={video_id}"
        r = requests.get(info_url, headers=BILIBILI_HEADERS, timeout=10)
        data = r.json()
        
        if data["code"] != 0:
            return None, None
        
        title = data["data"]["title"]
        cid = data["data"]["cid"]
        
        # 获取字幕列表
        subtitle_url = f"https://api.bilibili.com/x/player/v2?bvid={video_id}&cid={cid}"
        r = requests.get(subtitle_url, headers=BILIBILI_HEADERS, timeout=10)
        player_data = r.json()
        
        subtitles = player_data.get("data", {}).get("subtitle", {}).get("subtitles", [])
        
        if subtitles:
            # 优先使用中文字幕
            subtitle_info = None
            for sub in subtitles:
                if "zh" in sub.get("lan", ""):
                    subtitle_info = sub
                    break
            if not subtitle_info and subtitles:
                subtitle_info = subtitles[0]
            
            if subtitle_info:
                subtitle_file_url = "https:" + subtitle_info["subtitle_url"] if subtitle_info["subtitle_url"].startswith("//") else subtitle_info["subtitle_url"]
                r = requests.get(subtitle_file_url, headers=BILIBILI_HEADERS, timeout=10)
                subtitle_data = r.json()
                
                # 拼接字幕文本
                text = "\n".join([item["content"] for item in subtitle_data.get("body", [])])
                return text, title
        
        return None, title
    except Exception as e:
        print(f"  ⚠️ 获取字幕失败: {str(e)[:50]}")
        return None, None


def fetch_bilibili_audio(video_id, page_num=None):
    """下载B站视频音频 - 不使用postprocessor，手动调用FFmpeg"""
    import yt_dlp
    import subprocess
    
    tmp_dir = tempfile.mkdtemp()
    suffix = f"p{page_num}" if page_num else video_id
    output_base = os.path.join(tmp_dir, suffix)
    
    url = f"https://www.bilibili.com/video/{video_id}" + (f"?p={page_num}" if page_num else "")
    
    downloaded = [False]
    
    def progress_hook(d):
        if d['status'] == 'downloading' and not downloaded[0]:
            percent = d.get('_percent_str', '').strip()
            speed = d.get('_speed_str', '').strip()
            if percent:
                print(f"\r  -> 下载中: {percent} @ {speed}", end='', flush=True)
        elif d['status'] == 'finished' and not downloaded[0]:
            downloaded[0] = True
            print("\r  -> 下载完成，正在转换格式...    ")
    
    opts = {
        "format": "ba[ext=m4a]/ba/bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": output_base + ".%(ext)s",
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}],
        "keepvideo": False,
        "quiet": True,
        "no_warnings": True,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Referer": "https://www.bilibili.com",
            "Origin": "https://www.bilibili.com",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Sec-Ch-Ua": '"Chromium";v="125", "Not.A/Brand";v="24"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "video",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "cross-site",
        },
        "progress_hooks": [progress_hook],
        "socket_timeout": 30,
        "retries": 5,
        "extractor_retries": 5,
        "fragment_retries": 5,
    }
    
    cookies = os.path.abspath(COOKIES_FILE)
    if os.path.exists(cookies):
        opts["cookiefile"] = cookies
    
    max_retries = 5
    for attempt in range(max_retries):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                # 调试：打印格式信息
                if info:
                    print(f"  [调试] 格式: {info.get('ext')}, 大小: {info.get('filesize') or info.get('filesize_approx')}, 时长: {info.get('duration')}s")
            
            # yt-dlp已转为MP3，直接找 .mp3 文件
            mp3_path = output_base + ".mp3"
            if os.path.exists(mp3_path):
                print("  -> 格式转换完成")
                return mp3_path, tmp_dir
            
            # 降级查找：可能有扩展名的原始文件
            downloaded_file = None
            for f in os.listdir(tmp_dir):
                if f.startswith(suffix) and f.endswith(('.m4a', '.webm', '.mp3', '.opus')):
                    downloaded_file = os.path.join(tmp_dir, f)
                    break
            if not downloaded_file:
                for f in os.listdir(tmp_dir):
                    if f == suffix:
                        src = os.path.join(tmp_dir, f)
                        dst = src + ".m4a"
                        os.rename(src, dst)
                        downloaded_file = dst
                        break
            
            if not downloaded_file or not os.path.exists(downloaded_file):
                print(f"  ❌ 未找到下载的音频文件 (tmp_dir: {os.listdir(tmp_dir)})")
                return None, tmp_dir
            
            # 手动FFmpeg转换（降级路径）
            print("  -> 手动FFmpeg转换...")
            mp3_path = output_base + ".mp3"
            result = subprocess.run(
                ["ffmpeg", "-i", downloaded_file, "-codec:a", "libmp3lame", "-qscale:a", "2", "-y", mp3_path],
                capture_output=True, timeout=120
            )
            if result.returncode == 0 and os.path.exists(mp3_path):
                print("  -> 格式转换完成")
                return mp3_path, tmp_dir
            # 再试：直接复制音频流
            alt = output_base + "_raw.mp3"
            retry = subprocess.run(
                ["ffmpeg", "-i", downloaded_file, "-vn", "-acodec", "copy", "-y", alt],
                capture_output=True, timeout=120
            )
            if retry.returncode == 0 and os.path.exists(alt):
                os.rename(alt, mp3_path)
                print("  -> 音频流提取完成")
                return mp3_path, tmp_dir
            print(f"  ❌ FFmpeg转换失败")
            return None, tmp_dir
            
        except Exception as e:
            error_msg = str(e)
            if "412" in error_msg or "Precondition" in error_msg:
                wait_time = 15 * (attempt + 1) + random.randint(0, 5)
                print(f"\r  ⚠️ 412限流，等待{wait_time}秒后重试 ({attempt+1}/{max_retries})...", end='', flush=True)
                time.sleep(wait_time)
                continue
            elif "unable to obtain file audio codec" in error_msg.lower() or "ffprobe" in error_msg.lower():
                if attempt < 3:
                    print(f"\r  ⚠️ 无音频轨，尝试下载视频提取音频 ({attempt+1}/3)...", end='', flush=True)
                    # 切换为视频格式，移除postprocessor
                    opts["format"] = "best/bestvideo+bestaudio"
                    opts.pop("postprocessors", None)
                    opts.pop("keepvideo", None)
                    time.sleep(3)
                    continue
                print(f"\n  ❌ 此视频无可用音频轨，已跳过")
                return None, tmp_dir
            else:
                print(f"\n  ❌ 音频下载失败: {error_msg[:100]}")
                return None, tmp_dir
    
    global _bilibili_last_412
    _bilibili_last_412 = time.time()
    print(f"\n  ❌ 音频下载失败: 重试{max_retries}次后仍失败")
    return None, tmp_dir


def get_bilibili_text(url):
    """获取B站视频文本：优先字幕，其次音频转录"""
    video_id = extract_bilibili_video_id(url)
    if not video_id:
        return None, None
    
    # 提取分P参数 ?p=N
    page_num = None
    p_match = re.search(r'[?&]p=(\d+)', url)
    if p_match:
        page_num = int(p_match.group(1))
    
    print(f"📺 检测到 Bilibili 视频: {video_id}" + (f" (p={page_num})" if page_num else ""))
    
    # 优先尝试获取字幕
    print("  -> 尝试获取字幕...")
    subtitle_text, title = fetch_bilibili_subtitle(video_id)
    
    if subtitle_text:
        print(f"  ✅ 获取字幕成功：{len(subtitle_text)}字")
        return subtitle_text, title
    
    print("  ⚠️ 无可用字幕，尝试音频转录...")
    
    # 检查全局412冷却：如果之前被限流，等待60秒再试
    global _bilibili_last_412
    if _bilibili_last_412 > 0:
        elapsed = time.time() - _bilibili_last_412
        if elapsed < 60:
            wait = int(60 - elapsed) + random.randint(5, 15)
            print(f"  ⏳ 上次412限流仅{int(elapsed)}秒前，等待{wait}秒...")
            time.sleep(wait)
        _bilibili_last_412 = 0
    
    # 下载音频并转录
    audio_path, tmp_dir = fetch_bilibili_audio(video_id, page_num)
    if not audio_path:
        return None, title
    
    try:
        import whisper
        
        print("  -> 音频下载完成，开始语音转文字...")
        print("  -> 加载Whisper模型...")
        model = whisper.load_model("base", device="cpu")
        print("  -> 模型加载完成，开始转录（预计1-3分钟）...")
        
        result = model.transcribe(audio_path, language="zh")
        print("  ✅ 转录完成")
        return result["text"], title
    except Exception as e:
        print(f"  ❌ 转录失败: {str(e)[:100]}")
        return None, title
    finally:
        # 清理临时文件
        try:
            if os.path.exists(audio_path):
                os.remove(audio_path)
            if os.path.exists(tmp_dir):
                os.rmdir(tmp_dir)
        except:
            pass


def fetch_youtube_metadata(url):
    try:
        r = requests.get(
            f"https://www.youtube.com/oembed?url={url}&format=json",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        data = r.json()
        return data.get("title", ""), data.get("author_name", "")
    except Exception as e:
        print(f"  -> 获取元数据失败: {e}")
        return None, None


def fetch_with_ytdlp(video_id, output_path):
    import yt_dlp
    opts = {
        "format": "bestaudio/best",
        "outtmpl": output_path,
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}],
        "quiet": True,
        "no_warnings": True,
        "extractor_args": {"youtube": {"skip": ["dash", "hls"]}},
        # 使用多客戶端嘗試：先 android 再 tv，避免 bot 檢測
        "player_client": ["android", "tv"],
        "headers": {
            "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.230 Mobile Safari/537.36",
        },
    }
    if os.path.exists(r"C:\Program Files\nodejs\node.exe"):
        opts["js_runtimes"] = {"node": {"path": r"C:\Program Files\nodejs\node.exe"}}
    cookies = os.path.abspath(COOKIES_FILE)
    if os.path.exists(cookies):
        opts["cookiefile"] = cookies

    url = f"https://www.youtube.com/watch?v={video_id}"
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


def get_video_text(url):
    import yt_dlp
    import whisper

    video_id = extract_video_id(url)
    if not video_id:
        return None, None

    print(f"📺 检测到 YouTube 视频: {video_id}")
    title, author = fetch_youtube_metadata(url)
    if title:
        print(f"  标题: {title}")

    print("  -> 下载音频 + Whisper 转录...")
    tmp_dir = tempfile.mkdtemp()
    audio_path = os.path.join(tmp_dir, f"{video_id}.mp3")
    try:
        fetch_with_ytdlp(video_id, audio_path.replace(".mp3", ""))
        audio_file = audio_path
        if not os.path.exists(audio_file):
            base = audio_path.replace(".mp3", "")
            for ext in [".mp3", ".m4a", ".webm"]:
                f = base + ext
                if os.path.exists(f):
                    audio_file = f
                    break
        if not os.path.exists(audio_file):
            print("  ❌ 音频文件未生成")
            return None, None

        print("  -> 音频下载完成，开始语音转文字...")
        model = whisper.load_model("base", device="cpu")
        result = model.transcribe(audio_file, language="zh")
        print("  ✅ 转录完成")
        return result["text"], title

    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        if "Sign in" in msg or "bot" in msg.lower():
            print(f"  ❌ 下载失败: cookies 已过期或需要登录")
            print("  📝 提示：请重新导出 cookies.txt")
            print(f"  📝 详细信息: {msg[:200]}")
            return None, title
        else:
            print(f"  ❌ 下载失败: {msg[:200]}")
            return None, None
    except Exception as e:
        print(f"  ❌ 转录失败: {str(e)[:100]}")
        return None, None
    finally:
        for f in os.listdir(tmp_dir):
            try:
                os.remove(os.path.join(tmp_dir, f))
            except:
                pass
        try:
            os.rmdir(tmp_dir)
        except:
            pass


def fetch_content_with_jina(url):
    print(f"🔄 正在抓取网页内容: {url}")
    try:
        r = requests.get(
            f"https://r.jina.ai/{url}",
            headers={"Accept": "text/markdown", "User-Agent": "Mozilla/5.0"},
            timeout=30,
            verify=False,
        )
        r.raise_for_status()
        print("✅ 抓取成功！")
        return r.text
    except Exception as e:
        print(f"❌ 抓取失败: {e}")
        return None


SYSTEM_PROMPT = """你是一位知识整理专家。将音频转录文本整理为详细笔记。

【转录修正】
修正错别字、同音字、语义不通的句子，确保内容流畅可读。

【语言】
必须使用简体中文输出。

【详细程度要求】
1. **必须详细展开每个核心观点**，不要简单罗列标题
2. 对每个重要概念进行解释说明，包括：是什么、为什么重要、如何应用
3. 保留所有具体的例子、数据、案例分析
4. 记录关键的步骤、流程、方法论
5. 捕捉作者的观点逻辑链条，不要只给结论

【写作原则】
1. 全程干货，直接写结论，不要说"作者说""本文认为""视频中提到"等归属说明
2. 表格和大纲灵活使用：对比/分类/多维度信息用表格（2-3列），简单列举用无序列表
3. 保留所有重要内容，不要过度精简
4. **重要**：只总结本集独有的内容，不要重复其他集已经讲过的背景知识
5. **新增**：每个主要部分都要有2-3个具体的子要点展开

【标题规则】
- title 必须反映本集的核心主题，与其他集有明确区分（如：01.真人至人圣人贤人、02.现代人的欲望问题）
- 严禁在元数据后再写一级标题（# 标题），元数据后直接开始内容
- tags 最多 5 个，用逗号分隔的列表格式 [标签1, 标签2, ...]
- 例：title: 真人至人圣人贤人 ✅
- 例：title: 现代人的欲望问题 ✅
- 例 tags: [养生, 黄帝内经, 中医] ✅

【表格格式】（仅当使用表格时遵守）
- 表格前后必须有空行（**非常重要**）
- 表格本身不能有缩进（顶格写，前面不能有空格）
- 单元格内容严格控制在20字以内
- 列数控制在2-3列

【输出格式】
直接输出 Markdown，元数据在最顶部，后面直接开始内容，不准再写 # 一级标题：

---
title: 本集核心主题标题
tags: [标签1, 标签2]
date: YYYY-MM-DD
source: 原始标题
---

内容部分自由组织，不需要固定章节标题，根据内容特点选择最合适的呈现方式。可以是连续段落、列表、表格的组合。每个主要部分都要有详细的子要点展开，确保内容充实完整。"""


def summarize_with_llm(text, video_title=None, llm_model=None, llm_base_url=None, llm_api_key=None, max_retries=3):
    from core.config import get_llm_config
    config = _get_config()
    print("🧠 正在呼叫大模型进行总结...")
    llm_config = get_llm_config(llm_model, llm_base_url, llm_api_key)
    client = OpenAI(api_key=llm_config.api_key, base_url=llm_config.base_url)

    today = datetime.now().strftime("%Y-%m-%d")
    user_content = f"请总结以下内容：\n\n{text}"
    if video_title:
        user_content = f"【视频/本集主题】{video_title}\n今日日期：{today}\n\n{user_content}"
    else:
        user_content = f"今日日期：{today}\n\n{user_content}"

    # 调试：打印传给LLM的文本前100字
    print(f"  📝 传给LLM的文本长度：{len(text)}字")
    if text:
        print(f"  📝 文本前100字：{text[:100]}...")
    else:
        print("  ⚠️ 警告：传给LLM的文本为空！")

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=llm_config.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.5,
            )
            print("✅ 总结完成！")
            result = response.choices[0].message.content
            print(f"  📝 LLM返回内容前100字：{result[:100]}...")
            return result
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "cooldown" in error_msg.lower():
                wait_time = 30
                print(f"⏳ API限流冷却，等待{wait_time}秒后重试 ({attempt+1}/{max_retries})...")
                time.sleep(wait_time)
            elif "451" in error_msg or "censorship" in error_msg.lower():
                print(f"  ⚠️ 内容被AI审查拦截，尝试分段重试...")
                # 分半重试（可能是某段敏感词导致）
                mid = len(text) // 2
                for part, label in [(text[:mid], "上半部分"), (text[mid:], "下半部分")]:
                    try:
                        resp = client.chat.completions.create(
                            model=llm_config.model,
                            messages=[
                                {"role": "system", "content": SYSTEM_PROMPT},
                                {"role": "user", "content": f"请总结以下{label}内容：\n\n{part}"},
                            ],
                            temperature=0.5,
                        )
                        return resp.choices[0].message.content
                    except:
                        continue
                print(f"  ❌ 分段重试仍被拦截，跳过该视频")
                return None
            else:
                print(f"❌ LLM 请求失败: {e}")
                return None
    print(f"❌ LLM 请求失败: 重试{max_retries}次后仍失败")
    return None


def check_note_exists(title, inbox_path=None):
    """检查笔记是否已存在"""
    config = _get_config()
    if inbox_path is None:
        inbox_path = config.obsidian.path if hasattr(config, 'obsidian') and config.obsidian else r"D:\tool\obsidian\20玄学\奇门遁甲\荀爽"
    
    safe_title = re.sub(r'[\\/*?:"<>|]', "", title)
    file_path = os.path.join(inbox_path, f"{safe_title}.md")
    return os.path.exists(file_path)


def save_to_obsidian(content, inbox_path=None, custom_filename=None):
    config = _get_config()
    if inbox_path is None:
        inbox_path = config.obsidian.path if hasattr(config, 'obsidian') and config.obsidian else r"D:\tool\obsidian\20玄学\奇门遁甲\荀爽"

    # 如果提供了自定义文件名，直接使用
    if custom_filename:
        safe_title = re.sub(r'[\\/*?:"<>|]', "", custom_filename)
        note_content = content
    else:
        # 尝试从YAML frontmatter提取标题
        yaml_title_match = re.search(r"^title:\s*(.+)$", content, re.MULTILINE)
        if yaml_title_match:
            raw_title = yaml_title_match.group(1).strip()
            raw_title = re.sub(r'[\s_]*(结构化笔记|笔记|总结|整理)$', '', raw_title)
            safe_title = re.sub(r'[\\/*?:"<>|]', "", raw_title)
            note_content = content
        else:
            # 尝试从Markdown标题提取标题
            md_title_match = re.search(r"^# (.*?)$", content, re.MULTILINE)
            if md_title_match:
                raw_title = md_title_match.group(1).strip()
                raw_title = re.sub(r'[\s_]*(结构化笔记|笔记|总结|整理)$', '', raw_title)
                safe_title = re.sub(r'[\\/*?:"<>|]', "", raw_title)
                note_content = content
            else:
                # 尝试从旧格式【标题：...】提取标题
                title_match = re.search(r"【标题：(.*?)】", content)
                if title_match:
                    raw_title = title_match.group(1).strip()
                    safe_title = re.sub(r'[\\/*?:"<>|]', "", raw_title)
                    note_content = content.replace(title_match.group(0), f"# {safe_title}\n")
                else:
                    safe_title = f"未命名笔记_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    note_content = content

    file_path = os.path.join(inbox_path, f"{safe_title}.md")
    
    # 检查内容是否有效
    if not note_content or len(note_content.strip()) < 50:
        print(f"  ⚠️ 内容过短或为空，跳过保存")
        return False
    
    # 检查是否包含LLM拒绝信息
    reject_keywords = ["无法回答", "抱歉，我无法", "抱歉，我不能", "不支持这个请求", "无法提供这个", "我无法生成"]
    if any(keyword in note_content[:200] for keyword in reject_keywords):
        print(f"  ⚠️ LLM拒绝生成内容，跳过保存")
        print(f"  📝 LLM返回：{note_content[:200]}...")
        return False
    
    try:
        os.makedirs(inbox_path, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(note_content)
        print(f"🎉 已保存到 Obsidian：{file_path}")
        return True
    except Exception as e:
        print(f"❌ 保存失败: {e}")
        return False


def is_youtube_playlist(url):
    """检测是否为 YouTube 播放列表"""
    return "list=" in url or "/playlist" in url


def fetch_youtube_playlist(url):
    """获取 YouTube 播放列表中的所有视频 ID"""
    import yt_dlp
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "player_client": ["android"],
    }
    node_path = "C:/Program Files/nodejs/node.exe"
    if os.path.exists(node_path):
        opts["js_runtimes"] = {"node": {"path": node_path}}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return []
            entries = info.get("entries", [])
            videos = []
            for entry in entries:
                vid = entry.get("id", entry.get("url", ""))
                title = entry.get("title", "")
                if vid and title:
                    videos.append({"id": vid, "title": title})
            return videos
    except Exception as e:
        print(f"  ❌ 获取播放列表失败: {str(e)[:100]}")
        return []


def _transcribe_local_audio(audio_path):
    """转录本地音频文件"""
    import whisper
    print(f"  -> 加载Whisper模型...")
    model = whisper.load_model("base", device="cpu")
    print(f"  -> 模型加载完成，开始转录（预计1-3分钟）...")
    result = model.transcribe(audio_path, language="zh")
    print(f"  ✅ 转录完成")
    return result["text"]


def _process_local_audio(file_path, inbox, llm_model, llm_base_url, llm_api_key):
    """处理单个本地音频文件"""
    filename = os.path.basename(file_path)
    title = os.path.splitext(filename)[0]
    
    print(f"\n🎵 处理: {filename}")
    
    # 检查笔记是否已存在
    if check_note_exists(title, inbox):
        print(f"  ⏭️ 笔记已存在，跳过")
        return True
    
    try:
        raw_text = _transcribe_local_audio(file_path)
        if raw_text:
            summary = summarize_with_llm(raw_text[:20000], title, llm_model, llm_base_url, llm_api_key)
            if summary:
                save_to_obsidian(summary, inbox)
                return True
            else:
                print(f"  ❌ LLM总结失败")
                return False
        else:
            print(f"  ❌ 转录失败")
            return False
    except Exception as e:
        print(f"  ❌ 处理失败: {str(e)[:80]}")
        return False


def run(args):
    target_url = args.url
    inbox = getattr(args, "inbox", None)
    llm_model = getattr(args, "llm_model", None)
    llm_base_url = getattr(args, "llm_base_url", None)
    llm_api_key = getattr(args, "llm_api_key", None)

    print("=" * 40)
    print("VTM - 视频/网页/音频 → 结构化笔记")
    print("=" * 40)

    # 检测本地文件或文件夹
    if os.path.isfile(target_url) and target_url.lower().endswith(('.mp3', '.wav', '.m4a', '.ogg', '.flac', '.wma')):
        # 单个音频文件
        success = _process_local_audio(target_url, inbox, llm_model, llm_base_url, llm_api_key)
        print(f"\n{'='*40}")
        print(f"{'✅ 完成' if success else '❌ 失败'}")
        return
    
    if os.path.isdir(target_url):
        # 文件夹批量处理
        audio_files = sorted([
            os.path.join(target_url, f) for f in os.listdir(target_url)
            if f.lower().endswith(('.mp3', '.wav', '.m4a', '.ogg', '.flac', '.wma'))
            and os.path.isfile(os.path.join(target_url, f))
        ])
        
        if not audio_files:
            print(f"❌ 文件夹中没有音频文件: {target_url}")
            return
        
        print(f"📁 批量处理模式: {len(audio_files)} 个音频文件\n")
        
        checkpoint = load_checkpoint()
        success = 0
        failed = 0
        skipped = 0
        
        for idx, file_path in enumerate(audio_files, 1):
            filename = os.path.basename(file_path)
            ck_key = f"local_{filename}"
            
            if ck_key in checkpoint:
                print(f"  [{idx}/{len(audio_files)}] ⏭️ {filename}")
                skipped += 1
                continue
            
            print(f"\n[{idx}/{len(audio_files)}] {filename}")
            
            if _process_local_audio(file_path, inbox, llm_model, llm_base_url, llm_api_key):
                save_checkpoint(ck_key)
                success += 1
            else:
                failed += 1
            
            if idx < len(audio_files):
                time.sleep(2)
        
        print(f"\n{'='*40}")
        print(f"📊 批量处理完成")
        print(f"   成功: {success} 个")
        print(f"   失败: {failed} 个")
        print(f"   跳过: {skipped} 个")
        print(f"{'='*40}")
        return

    # 检测 YouTube 播放列表
    if is_youtube_playlist(target_url):
        print("📺 检测到 YouTube 播放列表，批量处理模式")
        videos = fetch_youtube_playlist(target_url)
        if not videos:
            print("❌ 未获取到播放列表视频")
            return
        
        checkpoint = load_checkpoint()
        print(f"   共 {len(videos)} 个视频，断点记录 {len(checkpoint)} 个\n")
        success = 0
        failed = 0
        skipped = 0
        
        for idx, video in enumerate(videos, 1):
            vid = video["id"]
            title = video["title"]
            
            if vid in checkpoint:
                print(f"  [{idx}/{len(videos)}] ⏭️ {title}")
                skipped += 1
                continue
            
            print(f"\n[{idx}/{len(videos)}] {title}")
            print(f"   视频ID: {vid}")
            
            if check_note_exists(title, inbox):
                print(f"   ⏭️ 笔记已存在，跳过")
                save_checkpoint(vid)
                skipped += 1
                continue
            
            try:
                video_url = f"https://www.youtube.com/watch?v={vid}"
                raw_text, video_title = get_video_text(video_url)
                
                if raw_text:
                    summary = summarize_with_llm(raw_text[:20000], video_title or title, llm_model, llm_base_url, llm_api_key)
                    if summary:
                        save_to_obsidian(summary, inbox)
                        save_checkpoint(vid)
                        success += 1
                    else:
                        print(f"   ❌ LLM总结失败")
                        failed += 1
                else:
                    print(f"   ❌ 获取文本失败")
                    failed += 1
            except Exception as e:
                print(f"   ❌ 处理失败: {str(e)[:50]}")
                failed += 1
            
            if idx < len(videos):
                time.sleep(5)
        
        print(f"\n{'='*40}")
        print(f"📊 播放列表处理完成")
        print(f"   成功: {success} 个")
        print(f"   失败: {failed} 个")
        print(f"   跳过: {skipped} 个")
        print(f"{'='*40}")
        return

    # 检测B站合集（URL直接包含合集参数）
    if is_bilibili_url(target_url) and is_bilibili_collection(target_url):
        print("📺 检测到B站合集，批量处理模式")
        videos = fetch_bilibili_collection(target_url)
        if not videos:
            print("❌ 未获取到合集视频列表")
            return
        
        checkpoint = load_checkpoint()
        print(f"   共 {len(videos)} 个视频，断点记录 {len(checkpoint)} 个\n")
        success = 0
        failed = 0
        skipped = 0
        
        consec_412 = 0
        for idx, video in enumerate(videos, 1):
            bvid = video["bvid"]
            title = video["title"]
            
            if bvid in checkpoint:
                print(f"  [{idx}/{len(videos)}] ⏭️ {title}")
                skipped += 1
                continue
            
            print(f"\n[{idx}/{len(videos)}] {title}")
            print(f"   BV号: {bvid}")
            
            if check_note_exists(title, inbox):
                print(f"   ⏭️ 笔记已存在，跳过")
                save_checkpoint(bvid)
                skipped += 1
                continue
            
            try:
                video_url = f"https://www.bilibili.com/video/{bvid}"
                raw_text, video_title = get_bilibili_text(video_url)
                
                if raw_text:
                    summary = summarize_with_llm(raw_text[:20000], video_title or title, llm_model, llm_base_url, llm_api_key)
                    if summary:
                        save_to_obsidian(summary, inbox)
                        save_checkpoint(bvid)
                        success += 1
                    else:
                        print(f"   ❌ LLM总结失败")
                        failed += 1
                else:
                    print(f"   ❌ 获取文本失败")
                    failed += 1
                consec_412 = 0
            except Exception as e:
                print(f"   ❌ 处理失败: {str(e)[:50]}")
                failed += 1
            
            # 自适应退避：连续412错误时增加等待时间
            if idx < len(videos):
                base_wait = 8 if consec_412 < 2 else 30
                wait = base_wait + random.randint(0, 5)
                if consec_412 >= 2:
                    print(f"   ⏳ 连续限流，等待{wait}秒...")
                time.sleep(wait)
        
        print(f"\n{'='*40}")
        print(f"📊 合集处理完成")
        print(f"   成功: {success} 个")
        print(f"   失败: {failed} 个")
        print(f"   跳过: {skipped} 个")
        print(f"{'='*40}")
        return

    # 单个B站视频 - 检测是否属于合集或分P
    if is_bilibili_url(target_url):
        video_id = extract_bilibili_video_id(target_url)
        if video_id:
            # 检测是否为分P视频
            is_multi_part, part_count, video_title = check_bilibili_multi_part(video_id)
            if is_multi_part:
                print(f"📺 检测到分P视频《{video_title}》，共 {part_count} 个分P")
                print(f"   批量处理模式\n")
                checkpoint = load_checkpoint()
                success = 0
                failed = 0
                skipped = 0
                
                for page_num in range(1, part_count + 1):
                    ck_key = f"{video_id}_p{page_num}"
                    if ck_key in checkpoint:
                        print(f"  [{page_num}/{part_count}] ⏭️ 分P {page_num}")
                        skipped += 1
                        continue
                    
                    print(f"\n[{page_num}/{part_count}] 处理分P {page_num}")
                    
                    try:
                        info = fetch_bilibili_video_info(video_id)
                        pages = info.get("pages", [])
                        if page_num <= len(pages):
                            cid = pages[page_num - 1]["cid"]
                            part_name = pages[page_num - 1].get("part", f"分P{page_num}")
                            print(f"   标题: {part_name}")
                            # 用实际分P编号替换标题中的序号（修复偏移）
                            numbered_part = re.sub(r'^\d+\.\s*', '', part_name)
                            clean_name = f"{page_num}.{numbered_part}"
                            
                            raw_text = fetch_bilibili_multi_part_text(video_id, page_num, cid)
                            
                            if raw_text:
                                summary = summarize_with_llm(raw_text[:20000], f"{video_title} - {part_name}", llm_model, llm_base_url, llm_api_key)
                                if summary:
                                    save_to_obsidian(summary, inbox, custom_filename=clean_name)
                                    save_checkpoint(ck_key)
                                    success += 1
                                else:
                                    print(f"   ❌ LLM总结失败")
                                    failed += 1
                            else:
                                print(f"   ❌ 获取文本失败")
                                failed += 1
                    except Exception as e:
                        print(f"   ❌ 处理失败: {str(e)[:50]}")
                        failed += 1
                    
                    if page_num < part_count:
                        time.sleep(random.randint(8, 15))
                
                print(f"\n{'='*40}")
                print(f"📊 分P视频处理完成")
                print(f"   成功: {success} 个")
                print(f"   失败: {failed} 个")
                print(f"   跳过: {skipped} 个")
                print(f"{'='*40}")
                return
            
            # 检测是否属于合集
            is_collection, season_title = check_bilibili_collection_from_api(video_id)
            if is_collection:
                collection_videos, _ = get_bilibili_collection_from_video(video_id)
                if collection_videos and len(collection_videos) > 1:
                    print(f"📺 检测到视频属于合集《{season_title}》，共 {len(collection_videos)} 集")
                    print(f"   批量处理模式\n")
                    checkpoint = load_checkpoint()
                    print(f"   断点记录 {len(checkpoint)} 个\n")
                    success = 0
                    failed = 0
                    skipped = 0
                    
                    for idx, video in enumerate(collection_videos, 1):
                        bvid = video["bvid"]
                        title = video["title"]
                        
                        if bvid in checkpoint:
                            print(f"  [{idx}/{len(collection_videos)}] ⏭️ {title}")
                            skipped += 1
                            continue
                        
                        print(f"\n[{idx}/{len(collection_videos)}] {title}")
                        print(f"   BV号: {bvid}")
                        
                        if check_note_exists(title, inbox):
                            print(f"   ⏭️ 笔记已存在，跳过")
                            save_checkpoint(bvid)
                            skipped += 1
                            continue
                        
                        try:
                            video_url = f"https://www.bilibili.com/video/{bvid}"
                            raw_text, video_title = get_bilibili_text(video_url)
                            
                            if raw_text:
                                summary = summarize_with_llm(raw_text[:20000], video_title or title, llm_model, llm_base_url, llm_api_key)
                                if summary:
                                    save_to_obsidian(summary, inbox)
                                    save_checkpoint(bvid)
                                    success += 1
                                else:
                                    print(f"   ❌ LLM总结失败")
                                    failed += 1
                            else:
                                print(f"   ❌ 获取文本失败")
                                failed += 1
                        except Exception as e:
                            print(f"   ❌ 处理失败: {str(e)[:50]}")
                            failed += 1
                        
                        if idx < len(collection_videos):
                            time.sleep(random.randint(8, 15))
                    
                    print(f"\n{'='*40}")
                    print(f"📊 合集处理完成")
                    print(f"   成功: {success} 个")
                    print(f"   失败: {failed} 个")
                    print(f"   跳过: {skipped} 个")
                    print(f"{'='*40}")
                    return

    # 单个视频/网页处理
    video_title = None
    if is_youtube_url(target_url):
        # 检查是否属于未识别的播放列表（&list= 被 shell 截断时）
        if not parse_qs(urlparse(target_url).query).get("list"):
            video_id = extract_video_id(target_url)
            if video_id:
                try:
                    import yt_dlp
                    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "player_client": ["android"]}
                    node_path = "C:/Program Files/nodejs/node.exe"
                    if os.path.exists(node_path):
                        opts["js_runtimes"] = {"node": {"path": node_path}}
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
                        pl_id = info.get("playlist_id")
                        pl_title = info.get("playlist_title", "")
                        pl_count = info.get("playlist_count", 0)
                        if pl_id and pl_count > 1:
                            print(f"  🔗 该视频属于播放列表「{pl_title}」共 {pl_count} 集")
                            print(f"  📝 批量全部请用引号包裹 URL 再带上 &list={pl_id}")
                except:
                    pass

        raw_text, video_title = get_video_text(target_url)
    elif is_bilibili_url(target_url):
        raw_text, video_title = get_bilibili_text(target_url)
    else:
        raw_text = fetch_content_with_jina(target_url)

    if raw_text:
        summary = summarize_with_llm(raw_text[:20000], video_title, llm_model, llm_base_url, llm_api_key)
        if summary:
            save_to_obsidian(summary, inbox)
