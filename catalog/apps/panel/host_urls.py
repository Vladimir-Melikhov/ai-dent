from django.urls import include, path

# Корневой urlconf хоста панели: namespace «panel» появляется только через include
urlpatterns = [path("", include("apps.panel.urls"))]
