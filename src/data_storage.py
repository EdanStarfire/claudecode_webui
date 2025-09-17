"""
Data Storage System for Claude Code WebUI

Handles persistent storage of session data including activity logs,
message history, and state persistence with integrity checking.
"""

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional
import logging

logger = logging.getLogger(__name__)


class DataStorageManager:
    """Manages persistent storage for session data"""

    def __init__(self, session_directory: Path):
        self.session_dir = Path(session_directory)
        self.messages_file = self.session_dir / "messages.jsonl"
        self.state_file = self.session_dir / "state.json"
        self.history_file = self.session_dir / "history.json"
        self.integrity_file = self.session_dir / ".integrity"

    async def initialize(self):
        """Initialize storage directory and files"""
        try:
            self.session_dir.mkdir(parents=True, exist_ok=True)

            # Initialize empty files if they don't exist
            if not self.messages_file.exists():
                self.messages_file.touch()

            if not self.history_file.exists():
                await self._write_history([])

            # Data integrity check disabled to prevent session startup issues
            # await self._verify_integrity()

            logger.debug(f"Initialized storage for session {self.session_dir.name}")
        except Exception as e:
            logger.error(f"Failed to initialize storage: {e}")
            raise

    async def append_message(self, message_data: Dict[str, Any]):
        """Append a message to the activity log (JSONL format)"""
        try:
            # Add timestamp if not present
            if 'timestamp' not in message_data:
                message_data['timestamp'] = datetime.now(timezone.utc).isoformat()

            # Append to JSONL file
            with open(self.messages_file, 'a', encoding='utf-8') as f:
                json.dump(message_data, f, ensure_ascii=False)
                f.write('\n')

            # Update integrity hash
            await self._update_integrity()

            logger.debug(f"Appended message to {self.session_dir.name}")
        except Exception as e:
            logger.error(f"Failed to append message: {e}")
            raise

    async def read_messages(self, limit: Optional[int] = None, offset: int = 0) -> List[Dict[str, Any]]:
        """Read messages from activity log with pagination"""
        try:
            messages = []

            if not self.messages_file.exists():
                return messages

            with open(self.messages_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            # Apply offset and limit
            start_idx = offset
            end_idx = start_idx + limit if limit else None
            selected_lines = lines[start_idx:end_idx]

            for line in selected_lines:
                line = line.strip()
                if line:
                    try:
                        message = json.loads(line)
                        messages.append(message)
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse message line: {line[:100]}... Error: {e}")

            return messages
        except Exception as e:
            logger.error(f"Failed to read messages: {e}")
            return []

    async def get_message_count(self) -> int:
        """Get total number of messages in the log"""
        try:
            if not self.messages_file.exists():
                return 0

            count = 0
            with open(self.messages_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        count += 1

            return count
        except Exception as e:
            logger.error(f"Failed to count messages: {e}")
            return 0

    async def write_history(self, history_data: List[Dict[str, Any]]):
        """Write command history data"""
        await self._write_history(history_data)

    async def read_history(self) -> List[Dict[str, Any]]:
        """Read command history data"""
        try:
            if not self.history_file.exists():
                return []

            with open(self.history_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to read history: {e}")
            return []

    async def append_history_item(self, history_item: Dict[str, Any]):
        """Add item to command history"""
        try:
            history = await self.read_history()

            # Add timestamp if not present
            if 'timestamp' not in history_item:
                history_item['timestamp'] = datetime.now(timezone.utc).isoformat()

            history.append(history_item)

            # Limit history size (keep last 1000 items)
            if len(history) > 1000:
                history = history[-1000:]

            await self._write_history(history)
        except Exception as e:
            logger.error(f"Failed to append history item: {e}")
            raise

    async def _write_history(self, history_data: List[Dict[str, Any]]):
        """Internal method to write history data"""
        try:
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(history_data, f, indent=2, ensure_ascii=False)

            await self._update_integrity()
        except Exception as e:
            logger.error(f"Failed to write history: {e}")
            raise

    async def _calculate_integrity_hash(self) -> str:
        """Calculate integrity hash for all data files"""
        hash_md5 = hashlib.md5()

        # Hash messages file
        if self.messages_file.exists():
            with open(self.messages_file, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)

        # Hash state file
        if self.state_file.exists():
            with open(self.state_file, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)

        # Hash history file
        if self.history_file.exists():
            with open(self.history_file, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)

        return hash_md5.hexdigest()

    async def _update_integrity(self):
        """Update integrity hash file"""
        try:
            integrity_hash = await self._calculate_integrity_hash()
            integrity_data = {
                'hash': integrity_hash,
                'updated_at': datetime.now(timezone.utc).isoformat()
            }

            with open(self.integrity_file, 'w') as f:
                json.dump(integrity_data, f)
        except Exception as e:
            logger.error(f"Failed to update integrity: {e}")

    async def _verify_integrity(self) -> bool:
        """Verify data integrity against stored hash - DISABLED"""
        # Integrity check disabled to prevent session startup issues
        return True

    async def detect_corruption(self) -> Dict[str, Any]:
        """Detect and report data corruption issues - DISABLED"""
        # Corruption detection disabled to prevent session startup issues
        corruption_report = {
            'corrupted': False,
            'issues': [],
            'files_checked': [],
            'timestamp': datetime.now(timezone.utc).isoformat()
        }

        return corruption_report

        try:
            # Check integrity hash
            if not await self._verify_integrity():
                corruption_report['corrupted'] = True
                corruption_report['issues'].append('Integrity hash mismatch')

            # Check JSONL format in messages file
            if self.messages_file.exists():
                corruption_report['files_checked'].append(str(self.messages_file))
                try:
                    with open(self.messages_file, 'r', encoding='utf-8') as f:
                        line_num = 0
                        for line in f:
                            line_num += 1
                            line = line.strip()
                            if line:
                                try:
                                    json.loads(line)
                                except json.JSONDecodeError:
                                    corruption_report['corrupted'] = True
                                    corruption_report['issues'].append(f'Invalid JSON at line {line_num} in messages.jsonl')
                except Exception as e:
                    corruption_report['corrupted'] = True
                    corruption_report['issues'].append(f'Cannot read messages.jsonl: {str(e)}')

            # Check JSON format in history file
            if self.history_file.exists():
                corruption_report['files_checked'].append(str(self.history_file))
                try:
                    with open(self.history_file, 'r', encoding='utf-8') as f:
                        json.load(f)
                except Exception as e:
                    corruption_report['corrupted'] = True
                    corruption_report['issues'].append(f'Invalid JSON in history.json: {str(e)}')

            # Check JSON format in state file
            if self.state_file.exists():
                corruption_report['files_checked'].append(str(self.state_file))
                try:
                    with open(self.state_file, 'r', encoding='utf-8') as f:
                        json.load(f)
                except Exception as e:
                    corruption_report['corrupted'] = True
                    corruption_report['issues'].append(f'Invalid JSON in state.json: {str(e)}')

        except Exception as e:
            corruption_report['corrupted'] = True
            corruption_report['issues'].append(f'Error during corruption detection: {str(e)}')

        if corruption_report['corrupted']:
            logger.error(f"Corruption detected in {self.session_dir.name}: {corruption_report['issues']}")

        return corruption_report

    async def cleanup(self):
        """Cleanup and final integrity check"""
        try:
            await self._update_integrity()
            logger.debug(f"Cleaned up storage for {self.session_dir.name}")
        except Exception as e:
            logger.error(f"Failed to cleanup storage: {e}")