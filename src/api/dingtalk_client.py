"""钉钉 API 客户端

实现钉钉 OAuth 2.0 免登流程：
1. 获取 access_token
2. 通过 authCode 获取 userid
3. 查询用户详情
"""
import time
from typing import Optional, Dict, Any
import requests

from core.service_config import api_endpoint, contact_user_agent, warn_missing


class DingTalkClient:
    """钉钉 API 客户端"""

    def __init__(self, app_key: str, app_secret: str, base_url: str | None = None):
        self.app_key = app_key
        self.app_secret = app_secret
        self.base_url = (base_url or api_endpoint("DINGTALK_BASE_URL", "dingtalk_base_url")).rstrip("/")
        if not self.base_url:
            warn_missing("DingTalk", "DINGTALK_BASE_URL")
            raise RuntimeError("钉钉 API endpoint 未配置")
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0

    def _get_access_token(self) -> str:
        """获取应用访问凭证 access_token

        文档: https://open.dingtalk.com/document/orgapp/api-gettoken
        """
        # 检查缓存的 token 是否还有效（提前 5 分钟刷新）
        if self._access_token and time.time() < self._token_expires_at - 300:
            return self._access_token

        url = f"{self.base_url}/gettoken"
        params = {
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }

        try:
            response = requests.get(url, params=params, timeout=10, headers={"User-Agent": contact_user_agent()})
            response.raise_for_status()
            data = response.json()

            if data.get("errcode") != 0:
                raise Exception(f"获取 access_token 失败: {data.get('errmsg')}")

            self._access_token = data["access_token"]
            # token 有效期 7200 秒（2 小时）
            self._token_expires_at = time.time() + data.get("expires_in", 7200)

            return self._access_token

        except requests.RequestException as e:
            raise Exception(f"请求钉钉 API 失败: {e}")

    def get_user_id_by_auth_code(self, auth_code: str) -> str:
        """通过免登授权码获取用户 userid

        文档: https://open.dingtalk.com/document/orgapp/obtain-the-userid-of-a-user-by-using-the-log-free

        Args:
            auth_code: 免登授权码（有效期 5 分钟，只能使用一次）

        Returns:
            用户 userid
        """
        access_token = self._get_access_token()

        url = f"{self.base_url}/topapi/v2/user/getuserinfo"
        params = {"access_token": access_token}
        payload = {"code": auth_code}

        try:
            response = requests.post(url, params=params, json=payload, timeout=10, headers={"User-Agent": contact_user_agent()})
            response.raise_for_status()
            data = response.json()

            if data.get("errcode") != 0:
                raise Exception(f"获取用户信息失败: {data.get('errmsg')}")

            result = data.get("result", {})
            userid = result.get("userid")

            if not userid:
                raise Exception("未找到用户 userid")

            return userid

        except requests.RequestException as e:
            raise Exception(f"请求钉钉 API 失败: {e}")

    def get_user_details(self, userid: str) -> Dict[str, Any]:
        """查询用户详情

        文档: https://open.dingtalk.com/document/orgapp/query-user-details

        Args:
            userid: 用户 userid

        Returns:
            用户详情字典
        """
        access_token = self._get_access_token()

        url = f"{self.base_url}/topapi/v2/user/get"
        params = {"access_token": access_token}
        payload = {"userid": userid}

        try:
            response = requests.post(url, params=params, json=payload, timeout=10, headers={"User-Agent": contact_user_agent()})
            response.raise_for_status()
            data = response.json()

            if data.get("errcode") != 0:
                raise Exception(f"查询用户详情失败: {data.get('errmsg')}")

            return data.get("result", {})

        except requests.RequestException as e:
            raise Exception(f"请求钉钉 API 失败: {e}")

    def get_user_by_auth_code(self, auth_code: str) -> Dict[str, Any]:
        """通过免登授权码获取用户完整信息

        组合调用 get_user_id_by_auth_code 和 get_user_details

        Args:
            auth_code: 免登授权码

        Returns:
            用户详情字典，包含 userid, name, avatar, dept_id_list 等
        """
        userid = self.get_user_id_by_auth_code(auth_code)
        return self.get_user_details(userid)

    def get_department_name(self, dept_id: int) -> str:
        """获取部门名称

        Args:
            dept_id: 部门ID

        Returns:
            部门名称
        """
        access_token = self._get_access_token()

        url = f"{self.base_url}/topapi/v2/department/get"
        params = {"access_token": access_token}
        payload = {"dept_id": dept_id}

        try:
            response = requests.post(url, params=params, json=payload, timeout=10, headers={"User-Agent": contact_user_agent()})
            response.raise_for_status()
            data = response.json()

            if data.get("errcode") != 0:
                return ""

            return data.get("result", {}).get("name", "")

        except requests.RequestException:
            return ""

    def get_user_departments(self, dept_id_list: list) -> list:
        """获取用户所有部门名称

        Args:
            dept_id_list: 部门ID列表

        Returns:
            部门名称列表
        """
        return [self.get_department_name(dept_id) for dept_id in dept_id_list]
