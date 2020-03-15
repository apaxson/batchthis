from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('addTest', views.batchTest, name='addTest'),
    path('addAddon', views.batchAddition, name='addAddon'),
    path('addNote', views.batchNote, name='addNote'),
    path('batch/<int:pk>', views.batch, name='batch'),
    path('batch/<int:pk>/addTest', views.batchTest, name='addDetailTest'),
    path('batch/<int:pk>/addNote', views.batchNote, name='addDetailNote'),
    path('batch/<int:pk>/addAddon', views.batchAddition, name="addDetailAddon"),
    path('batch/<int:pk>/batchGraphs', views.batchGraphs, name='batchGraphs')
]