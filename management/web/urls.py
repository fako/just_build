"""
URL configuration for web project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from ninja import NinjaAPI

from access_control.authentication import api_key_auth
from access_control.router import router as access_control_router
from runtimes.router import router as runtimes_router


api = NinjaAPI(
    version="0.0.1",
    title="Just Build Management API",
    # Authenticated by default, so a new controller cannot be added without an explicit decision.
    auth=api_key_auth,
)
# Add main router
api.add_router("", access_control_router)
api.add_router("", runtimes_router)


urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/', api.urls),
]
