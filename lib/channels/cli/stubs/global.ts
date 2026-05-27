import os from "node:os"
import path from "node:path"

export const Global = {
  Path: {
    state: path.join(os.homedir(), ".lumen", "state"),
    config: path.join(os.homedir(), ".lumen", "config"),
    home: path.join(os.homedir(), ".lumen"),
  },
}
