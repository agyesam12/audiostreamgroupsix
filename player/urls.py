from django.urls import path
from . import views

urlpatterns = [
    path('',                    views.index,          name='index'),
    path('docs',                views.docs,           name='docs'),
    path('api/server/start',    views.server_start),
    path('api/server/stop',     views.server_stop),
    path('api/rtsp/connect',    views.rtsp_connect),
    path('api/rtsp/describe',   views.rtsp_describe),
    path('api/rtsp/setup',      views.rtsp_setup),
    path('api/rtsp/play',       views.rtsp_play),
    path('api/rtsp/pause',      views.rtsp_pause),
    path('api/rtsp/teardown',   views.rtsp_teardown),
    path('api/status',          views.api_status),
    path('api/audio',           views.audio_serve),
]
