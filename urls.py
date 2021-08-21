from django.urls import path, include
from . import views


urlpatterns = [
    path('', views.index, name='index'),
    path('addTest', views.batchTest, name='addTest'),
    path('addAddon', views.batchAddition, name='addAddon'),
    path('addNote', views.batchNote, name='addNote'),
    path('batches', views.batchListing, name='batchListing'),
    path('batch/<int:pk>', views.batch, name='batch'),
    path('batch/<int:pk>/addTest', views.batchTest, name='addDetailTest'),
    path('batch/<int:pk>/addNote', views.batchNote, name='addDetailNote'),
    path('batch/<int:pk>/addNote/<str:noteType>', views.batchNote, name='addDetailNoteType'),
    path('batch/<int:pk>/addAddon', views.batchAddition, name="addDetailAddon"),
    path('batch/<int:pk>/batchGraphs', views.batchGraphs, name='batchGraphs'),
    path('batch/<int:pk>/activity', views.activity, name="batchActivity"),
    path('utils/refractometerCorrection', views.refractometerCorrection, name="refractometerCorrection"),
    #TODO: Add Fermenter Page/Template
    #TODO: Add Recipe Import Page/Template
]