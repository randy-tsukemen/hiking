"""yama MCP server：把登山規劃資料層開放給任何 MCP client（Agent）。

工具與 LINE bot 共用同一份 `yama.agent_tools`：
山岳資料（含 Yamap 路線難度數據）、山頂 16 天天氣、
毎日あるぺん号巴士方案（含預約連結）、套裝房間空位實查、天氣排名。

啟動（stdio）：
    yama-mcp
或免安裝：
    uvx --from git+https://github.com/randy-tsukemen/hiking yama-mcp

Claude Desktop / 一般 MCP client 設定範例：
    {
      "mcpServers": {
        "yama": {
          "command": "uvx",
          "args": ["--from", "git+https://github.com/randy-tsukemen/hiking", "yama-mcp"]
        }
      }
    }
"""

from mcp.server.fastmcp import FastMCP

from . import agent_tools

app = FastMCP(
    "yama",
    instructions=(
        "日本登山規劃工具（東京出發）。查詢山岳資料、Yamap 路線難度數據、"
        "山頂 16 天天氣適宜度、毎日あるぺん号夜行巴士（含直達預約連結）、"
        "巴士套裝的山屋房間空位實查。"
        "重要：巴士的「受付中/催行決定」不代表山屋房間有空位，"
        "推薦含山屋套裝前請用 check_hut_room_availability 確認。"
        "推薦巴士方案時，請同時附上方案詳細頁"
        "（https://bus.maitabi.jp/detail.html?course_no=N）與預約連結。"
    ),
)

for fn in agent_tools.TOOL_FUNCTIONS:
    app.tool()(fn)


def main() -> None:
    app.run()


if __name__ == "__main__":
    main()
