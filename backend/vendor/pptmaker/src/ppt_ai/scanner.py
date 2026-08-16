from pptx import Presentation

from src.ppt_ai.logger import logger


class PresentationScanner:

    def __init__(self, ppt_path):

        self.ppt_path = ppt_path
        self.prs = None

    def load(self):

        logger.info(f"Loading {self.ppt_path}")

        self.prs = Presentation(self.ppt_path)

        logger.info(f"{len(self.prs.slides)} slides found")

        return self.prs
