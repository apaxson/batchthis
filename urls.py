from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('addTest', views.batchTest, name='addTest'),
    path('batch/<int:pk>', views.batch, name='batch')
]