from ninja import Router

from runtimes.controllers.runtime import controller as runtime_controller


router = Router()
router.add_router("runtimes", runtime_controller)
