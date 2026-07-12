#!/usr/bin/env python3
"""
Run once before the presentation to convert all MP3s to WAV files.

Usage:
    python convert_songs.py

This creates puul.wav, do_better.wav, and biggest_nathaniel.wav
in the project root. After that, switching songs in the dashboard
is instant (no reconversion needed).
"""
import os
import sys
import wave
import subprocess

# Force UTF-8 output on Windows so Unicode filenames don't crash prints
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SONGS = {
    'puul': {
        'title':  'Puul',
        'artist': 'Lasmid',
        'mp3':    'Lasmid - Puul (Official Video) - Lasmid (youtube).mp3',
        'wav':    'puul.wav',
    },
    'do_better': {
        'title':  'Do Better',
        'artist': 'Kuami Eugene',
        'mp3':    'Kuami Eugene - Do Better - Kuami Eugene (youtube).mp3',
        'wav':    'do_better.wav',
    },
    'biggest_nathaniel': {
        'title':  'Biggest Nathaniel',
        'artist': 'Lasmid',
        'mp3':    'Lasmid - Biggest Nathaniel (Official Lyrics Video) - AMB StudiOS (youtube).mp3',
        'wav':    'biggest_nathaniel.wav',
    },
}


def convert(mp3_path, wav_path, title):
    # Method 1: miniaudio
    try:
        import miniaudio
        print('  Using miniaudio...')
        decoded = miniaudio.decode_file(
            mp3_path,
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=1,
            sample_rate=44100,
        )
        raw = bytes(decoded.samples)
        with wave.open(wav_path, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(44100)
            wf.writeframes(raw)
        size_mb = os.path.getsize(wav_path) / (1024 * 1024)
        print('  OK -> %s  (%.1f MB)' % (wav_path, size_mb))
        return True
    except ImportError:
        print('  miniaudio not installed -- trying pydub...')
    except Exception as e:
        print('  miniaudio error: %s -- trying pydub...' % str(e)[:120])

    # Method 2: pydub
    try:
        from pydub import AudioSegment
        print('  Using pydub...')
        seg = AudioSegment.from_mp3(mp3_path)
        seg = seg.set_channels(1).set_frame_rate(44100).set_sample_width(2)
        seg.export(wav_path, format='wav')
        size_mb = os.path.getsize(wav_path) / (1024 * 1024)
        print('  OK -> %s  (%.1f MB)' % (wav_path, size_mb))
        return True
    except ImportError:
        print('  pydub not installed -- trying ffmpeg...')
    except Exception as e:
        print('  pydub error: %s -- trying ffmpeg...' % str(e)[:120])

    # Method 3: ffmpeg subprocess
    try:
        print('  Using ffmpeg...')
        result = subprocess.run(
            ['ffmpeg', '-y', '-i', mp3_path,
             '-ac', '1', '-ar', '44100', '-acodec', 'pcm_s16le', wav_path],
            capture_output=True, timeout=300,
        )
        if result.returncode == 0:
            size_mb = os.path.getsize(wav_path) / (1024 * 1024)
            print('  OK -> %s  (%.1f MB)' % (wav_path, size_mb))
            return True
        err_out = result.stderr.decode(errors='replace')[:200]
        print('  ffmpeg returned non-zero: %s' % err_out)
    except FileNotFoundError:
        print('  ffmpeg not found in PATH.')
    except Exception as e:
        print('  ffmpeg error: %s' % str(e)[:120])

    return False


def main():
    print('=' * 60)
    print('Group 6 -- MP3 to WAV Converter')
    print('=' * 60)

    any_failed = False
    for key, song in SONGS.items():
        mp3_path = os.path.join(BASE_DIR, song['mp3'])
        wav_path = os.path.join(BASE_DIR, song['wav'])

        print('\n[%s -- %s]' % (song['title'], song['artist']))

        if os.path.exists(wav_path):
            size_mb = os.path.getsize(wav_path) / (1024 * 1024)
            print('  Already converted (%.1f MB) -- skipping.' % size_mb)
            continue

        if not os.path.exists(mp3_path):
            print('  MP3 not found: %s' % song['mp3'])
            any_failed = True
            continue

        ok = convert(mp3_path, wav_path, song['title'])
        if not ok:
            any_failed = True
            print('\n  FAILED to convert %s.' % song['title'])
            print('  Install at least one of:')
            print('    pip install miniaudio')
            print('    pip install pydub      (also needs ffmpeg)')
            print('    Install ffmpeg from https://ffmpeg.org/download.html')

    print()
    if any_failed:
        print('Some conversions failed -- check errors above.')
        sys.exit(1)
    else:
        print('All songs ready!  You can now launch the Django server.')


if __name__ == '__main__':
    main()
