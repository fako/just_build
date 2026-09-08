from ninja import Router

from workflows.controllers.workflow import controller as workflow_controller


router = Router()
router.add_router("workflows", workflow_controller)
