from ninja import Router

from access_control.controllers.workspace import controller as workspace_controller


router = Router()
router.add_router("workspaces", workspace_controller)
