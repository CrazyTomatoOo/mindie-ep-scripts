#!/usr/bin/env python3
"""
整合后的配置合并与部署脚本

功能：
1. 合并基础配置与环境变量映射
2. 生成最终配置文件
3. 执行部署流程

设计原则：
- 单一职责：每个函数只做一件事
- 防御式编程：严格的输入验证和错误处理
- 可维护性：清晰的类型注解和文档字符串
- 安全性：防范注入攻击和异常输入
"""

import os
import signal
import sys
import json
import logging
import argparse
import subprocess
import time
import atexit
import shutil
from typing import Any, Dict, Optional, Union
from collections import deque
from jsonpath_ng import parse

# ----------------------------
# 常量配置
# ----------------------------
DEFAULT_CONFIG = {
    "NAMESPACE": "model-engine",
    "INSTANCE_NAME": "deepseek",
    "POD_IP": "127.0.0.1",
    "EXPERT_MAP_FILE": "",
    "MODEL_WEIGHT_PATH": "/mnt/remote/models",
    "IMAGE_NAME": "mindie-ucm-me:25.5.0",
    "MINDIE_ENV_FILE": "/home/modellite/conf/mindie_env.json",
    "INPUT_USER_CONFIG": "/home/modellite/conf/user_config.json",
    "OUTPUT_USER_CONFIG": "/home/modellite/kubernetes_deploy_scripts/user_config.json",
    "PREDICT_PORT": "1025",
    "MANAGE_PORT": "1026",
    "LOG_PATH": "/mnt/remote/model-lite/inference",
    "DEPLOY_SCRIPTS_DIR": "/home/modellite/kubernetes_deploy_scripts",
    "ENV_MAPPING_FILE": "/home/modellite/scripts/env_mapping.json",
    "TLS_ENABLE": "false",
    "DEPLOY_MOUNT_PATH": "{}",
    "UCCONFIG_SOURCE": "/home/modellite/conf/ucconfig.json",
    "UCCONFIG_DEST": "/home/modellite/kubernetes_deploy_scripts/ucconfig.json",
}

# 安全限制
MAX_JSON_DEPTH = 50
MAX_JSON_SIZE = 10000  # 10KB

# ----------------------------
# 日志配置
# ----------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


# ----------------------------
# 核心功能类
# ----------------------------
class ConfigManager:
    """配置管理核心类"""

    @staticmethod
    def sanitize_value(value: Any) -> Any:
        """
        安全处理输入值
        Args:
            value: 需要处理的值
        Returns:
            处理后的安全值
        """
        if isinstance(value, str):
            return (
                value.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&#x27;")
            )
        return value

    @staticmethod
    def safe_json_load(file_path: str) -> Dict:
        """
        安全加载JSON文件
        Args:
            file_path: JSON文件路径
        Returns:
            解析后的字典
        Raises:
            ValueError: 当文件格式错误或超过限制时
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Config file not found: {file_path}")

        with open(file_path, 'r') as f:
            content = f.read(MAX_JSON_SIZE + 1)

        if len(content) > MAX_JSON_SIZE:
            raise ValueError(f"JSON file exceeds maximum size ({MAX_JSON_SIZE} bytes)")

        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON format in {file_path}: {str(e)}")

    @staticmethod
    def deep_merge(base: Dict, override: Dict) -> Dict:
        """
        深度合并两个字典
        Args:
            base: 基础配置
            override: 需要合并的配置
        Returns:
            合并后的字典
        """
        result = base.copy()
        stack = deque([(result, override)])

        while stack:
            current_base, current_override = stack.pop()

            for key, value in current_override.items():
                if key in current_base and isinstance(current_base[key], dict) and isinstance(value, dict):
                    stack.append((current_base[key], value))
                else:
                    current_base[key] = ConfigManager.sanitize_value(value)
        return result

    @staticmethod
    def apply_env_mappings(base_config: Dict, mappings: Dict[str, str], env_config: Dict, strict: bool = False) -> Dict:
        """
        应用环境变量映射到配置
        Args:
            base_config: 基础配置
            mappings: 映射规则 {json_path: env_var}
            env_config: 环境变量读入后的配置
            strict: 是否严格模式
        Returns:
            更新后的配置
        """
        for json_path, env_var in mappings.items():
            try:
                expr = parse(json_path)
                matches = expr.find(base_config)

                if not matches:
                    if strict:
                        raise KeyError(f"Path not found: {json_path}")
                    logger.warning(f"Path not found: {json_path}")
                    continue

                env_value = env_config.get(env_var)
                if env_value is None:
                    logger.warning(f"Env var not set: {env_var}")
                    continue

                for match in matches:
                    original = match.value
                    converted = ConfigManager.convert_type(env_value, original)
                    match.full_path.update(base_config, converted)

                logger.debug(f"Updated {json_path} from {env_var}")

            except Exception as e:
                if strict:
                    raise
                logger.warning(f"Skipping {json_path}: {str(e)}")
        return base_config

    @staticmethod
    def convert_type(value: str, original: Any) -> Any:
        """
        类型转换辅助函数
        Args:
            value: 字符串值
            original: 原始值（用于类型推断）
        Returns:
            转换后的值
        """
        if original is None:
            return value

        try:
            if isinstance(original, bool):
                return value.lower() in ('true', 'yes', '1')
            elif isinstance(original, int):
                return int(value)
            elif isinstance(original, float):
                return float(value)
            elif isinstance(original, (list, dict)):
                try:
                    return json.loads(value)
                except json.JSONDecodeError:
                    return original
            return value
        except (ValueError, TypeError):
            logger.warning(f"Type conversion failed for: {value}")
            return original


# ----------------------------
# 部署执行类
# ----------------------------
class DeploymentExecutor:
    """部署流程执行器"""

    @staticmethod
    def run_deployment(config: Dict) -> bool:
        """
        执行部署命令
        Args:
            config: 合并后的配置
        Returns:
            是否成功
        """
        try:
            deploy_script = os.path.join(
                config["DEPLOY_SCRIPTS_DIR"],
                "deploy_ac_job.py"
            )

            cmd = [
                "python3", deploy_script,
                "--conf_path", os.path.join(config["DEPLOY_SCRIPTS_DIR"], "conf"),
                "--deploy_yaml_path", os.path.join(config["DEPLOY_SCRIPTS_DIR"], "deployment"),
                "--output_path", os.path.join(config["DEPLOY_SCRIPTS_DIR"], "output"),
                "--user_config_path", config["OUTPUT_USER_CONFIG"]
            ]

            logger.info(f"Executing: {' '.join(cmd)}")
            subprocess.run(cmd, check=True)
            return True

        except subprocess.CalledProcessError as e:
            logger.error(f"Deployment failed with code {e.returncode}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            return False

    @staticmethod
    def run_uninstall(config: Dict) -> bool:
        """
        执行卸载命令
        Args:
            config: 配置字典，需包含 NAMESPACE 和 DEPLOY_SCRIPTS_DIR
        Returns:
            是否成功
        """
        try:
            delete_script = os.path.join(
                config["DEPLOY_SCRIPTS_DIR"],
                "delete.sh"
            )
            namespace = config.get("NAMESPACE", "default")
            cmd = ["bash", delete_script, namespace]

            logger.info(f"Executing uninstall: {' '.join(cmd)}")
            subprocess.run(cmd, check=True)
            return True

        except subprocess.CalledProcessError as e:
            logger.error(f"Uninstall failed with code {e.returncode}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during uninstall: {str(e)}")
            return False


def shutdown_handler(signum, frame):
    logger.info("Shutting down...")
    sys.exit(0)


# ----------------------------
# 主流程
# ----------------------------
def main(args: argparse.Namespace) -> int:
    """
    主执行流程
    Args:
        args: 命令行参数
    Returns:
        退出状态码
    """
    try:
        # 1. 初始化配置
        config = DEFAULT_CONFIG.copy()
        config.update({k: v for k, v in os.environ.items() if k in DEFAULT_CONFIG})

        # 2. 加载基础配置
        base_config = ConfigManager.safe_json_load(args.base_file)
        # 2.5 拷贝 ucconfig.json 到部署脚本目录
        ucconfig_source = config.get("UCCONFIG_SOURCE")
        ucconfig_dest = config.get("UCCONFIG_DEST")
        if ucconfig_source and os.path.exists(ucconfig_source):
            try:
                shutil.copy2(ucconfig_source, ucconfig_dest)
                logger.info(f"Copied ucconfig.json from {ucconfig_source} to {ucconfig_dest}")
            except Exception as e:
                logger.warning(f"Failed to copy ucconfig.json: {e}")
        else:
            logger.info(f"ucconfig.json not found at {ucconfig_source}, skipping copy")

        # 3. 加载映射配置
        try:
            mappings = ConfigManager.safe_json_load(args.mapping)
        except Exception as e:
            if args.strict:
                raise
            logger.warning(f"Using empty mappings: {str(e)}")
            mappings = {}

        # 4. 应用环境变量
        merged_config = ConfigManager.apply_env_mappings(base_config, mappings, config, args.strict)

        # 5. 保存结果
        with open(args.output, 'w') as f:
            json.dump(merged_config, f, indent=2)
        logger.info(f"Config saved to {args.output}")

        # 6. 执行部署
        if args.execute:
            atexit.register(lambda: DeploymentExecutor.run_uninstall(config))
            if not DeploymentExecutor.run_deployment(config):
                return 1
            signal.signal(signal.SIGINT, shutdown_handler)
            signal.signal(signal.SIGTERM, shutdown_handler)
            while True:
                time.sleep(3600)

        return 0

    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Configuration merger and deployment tool",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("base_file", help="Base configuration file")
    parser.add_argument("--mapping", default="env_mapping.json", help="Environment mapping file")
    parser.add_argument("--output", default="merged.json", help="Output file path")
    parser.add_argument("--strict", action="store_true", help="Enable strict mode")
    parser.add_argument("--execute", action="store_true", help="Execute deployment after merge")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    if args.debug:
        logger.setLevel(logging.DEBUG)

    sys.exit(main(args))
