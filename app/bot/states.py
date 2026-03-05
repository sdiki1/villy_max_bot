from maxapi.context.state_machine import State, StatesGroup


class OrderStates(StatesGroup):
    waiting_phone = State()
    waiting_full_name = State()
    waiting_product = State()
    waiting_mug_type = State()
    waiting_product_size = State()
    waiting_source = State()
    waiting_image = State()
    waiting_design_notes = State()


class FAQStates(StatesGroup):
    selecting_question = State()


class SupportStates(StatesGroup):
    active_chat = State()
