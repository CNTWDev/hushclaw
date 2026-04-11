"""Tools for the stock-query skill."""
from __future__ import annotations
from hushclaw.tools.base import ToolResult, tool
import os

API_KEY = "10a81138c0b941609263fd05300562302e16982eefdd43729298017334dcf298"

@tool(description="查询指定股票的当前实时价格。输入股票代码（如 'AAPL'）。")
def stock_get_price(symbol: str) -> ToolResult:
    try:
        from itick.sdk import Client
        client = Client(api_key=API_KEY)
        data = client.get_price(symbol)
        return ToolResult.ok(f"股票 {symbol} 当前价格: {data}")
    except ImportError:
        return ToolResult.error("itick.sdk 库未安装或导入路径错误。")
    except Exception as e:
        return ToolResult.error(f"查询失败: {str(e)}")

@tool(description="查询指定股票的历史交易数据。输入股票代码。")
def stock_get_history(symbol: str) -> ToolResult:
    try:
        from itick.sdk import Client
        client = Client(api_key=API_KEY)
        data = client.get_history(symbol)
        return ToolResult.ok(f"股票 {symbol} 历史数据: {data}")
    except ImportError:
        return ToolResult.error("itick.sdk 库未安装或导入路径错误。")
    except Exception as e:
        return ToolResult.error(f"查询失败: {str(e)}")
