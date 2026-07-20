from django.urls import path
from . import views

urlpatterns = [
    path('',                    views.index,          name='index'),
    path('docs',                views.docs,           name='docs'),
    path('walkthrough',         views.walkthrough,    name='walkthrough'),
    path('voice',               views.voice_page,     name='voice'),
    path('api/ws/start',        views.ws_relay_start),
    path('api/ws/stop',         views.ws_relay_stop),
    path('api/server/start',    views.server_start),
    path('api/server/stop',     views.server_stop),
    path('api/rtsp/connect',    views.rtsp_connect),
    path('api/rtsp/describe',   views.rtsp_describe),
    path('api/rtsp/setup',      views.rtsp_setup),
    path('api/rtsp/play',       views.rtsp_play),
    path('api/rtsp/pause',      views.rtsp_pause),
    path('api/rtsp/teardown',   views.rtsp_teardown),
    path('api/status',              views.api_status),
    path('api/audio',               views.audio_serve),
    path('api/song/select',         views.song_select),
    path('api/mic/server/start',    views.mic_server_start),
    path('api/mic/server/stop',     views.mic_server_stop),
    path('api/mic/client/start',    views.mic_client_start),
    path('api/mic/client/stop',     views.mic_client_stop),
    # Server mic → phone browser streaming
    path('api/server-mic/start',    views.start_server_mic),
    path('api/server-mic/stop',     views.stop_server_mic),
    path('api/server-mic/stream',   views.server_mic_stream),
    # Phone browser mic → laptop speakers
    path('api/mic-receive',         views.mic_receive),
]
