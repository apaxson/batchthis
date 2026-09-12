from django.urls import path, include
from . import views


urlpatterns = [
    path('', views.index, name='index'),
    path('addTest', views.batchTest, name='addTest'),
    path('addAddon', views.batchAddition, name='addAddon'),
    path('addNote', views.batchNote, name='addNote'),
    path('batches', views.batchListing, name='batchListing'),
    path('addBatch', views.addBatch, name='addBatch'),
    path('addRecipe', views.addRecipe, name='addRecipe'),
    path('addRecipe/<int:pk>', views.addRecipe, name='editRecipe'),
    path('batch/<int:pk>', views.batch, name='batch'),
    path('batch/<int:pk>/edit', views.addBatch, name="editBatch"),
    path('recipe/<int:pk>', views.addRecipe), #TODO create views.recipe and update view
    path('recipe/<int:pk>/view', views.addRecipe, name="recipe"), #TODO create views.recipe and update view
    path('recipe/<int:pk>/edit', views.addRecipe, name="editRecipe"),
    #path('recipe/<int:pk>/editFermentables', views.editFermentables),
    #path('recipe/<int:pk>/editFermentables', views.editModelFermentables),
    path('recipe/<int:pk>/editFermentables', views.editFermentables, name="editFermentables"),
    path('recipe/<int:pk>/editAdjuncts', views.editAdjuncts, name='editAdjuncts'),
    path('recipe/<int:pk>/editYeasts', views.editYeasts, name='editYeasts'),
    path('recipes', views.recipeListing, name="recipeListing"),
    path('batch/<int:pk>/addTest', views.batchTest, name='addDetailTest'),
    path('batch/<int:pk>/addNote', views.batchNote, name='addDetailNote'),
    path('batch/<int:pk>/addNote/<str:noteType>', views.batchNote, name='addDetailNoteType'),
    path('batch/<int:pk>/addAddon', views.batchAddition, name="addDetailAddon"),
    path('batch/<int:pk>/batchGraphs', views.batchGraphs, name='batchGraphs'),
    path('batch/<int:pk>/activity', views.activity, name="batchActivity"),
    path('utils/refractometerCorrection', views.refractometerCorrection, name="refractometerCorrection"),
    path('rpc/categoryFilterByStyle', views.categoryFilterByStyle, name="categoryFilterByStyle"),
    path('rpc/getDataFromRecipe', views.getDataFromRecipe, name="getDataFromRecipe"),
    path('rpc/utils/<str:action>/', views.utilities, name="utils"),
    path('admin/import', views.admin_import, name="admin_import")
    #TODO: Add Fermenter Page/Admin Template
]