from dotenv import load_dotenv
from pathlib import Path

# NoneBot reads its own configuration, while the plugins also use os.environ.
# Load the project .env before either one is initialized.
load_dotenv(Path(__file__).with_name(".env"))

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

nonebot.init()

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

nonebot.load_plugins(str(Path(__file__).resolve().parent / "plugins"))

if __name__ == "__main__":
    nonebot.run()
