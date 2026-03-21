from ninja import Router

from access_control.controllers.project import controller as project_controller


router = Router()
router.add_router("projects", project_controller)
