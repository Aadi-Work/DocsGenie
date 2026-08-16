"""
Natural Language Command Interpreter

Converts natural language commands into structured update operations.
Enables users to issue commands like "Update Current Status with these three points"
or "Change all OFF TRACK projects to AT RISK".
"""

import re
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass

from src.ppt_ai.change_plan import UpdateOperation, OperationType, OperationBuilder
from src.ppt_ai.document_tree import DocumentTree, ElementType


@dataclass
class ParsedCommand:
    """Result of parsing a natural language command."""
    command_type: str  # "update", "replace", "add", "delete", "change", etc.
    target: str  # What to modify
    action: str  # What to do
    value: str  # New value or content
    context: Dict[str, str]  # Additional context
    confidence: float  # 0-1 confidence in parse


class CommandParser:
    """Parses natural language commands."""
    
    # Command patterns
    UPDATE_PATTERNS = [
        r"update\s+(.+?)\s+(?:to|with)\s+(.+)$",
        r"change\s+(.+?)\s+to\s+(.+)$",
        r"set\s+(.+?)\s+to\s+(.+)$",
        r"modify\s+(.+?)\s+to\s+(.+)$",
    ]
    
    ADD_PATTERNS = [
        r"add\s+(.+?)\s+(?:to|under|under|in)\s+(.+)$",
        r"append\s+(.+?)\s+(?:to|under)\s+(.+)$",
        r"insert\s+(.+?)\s+(?:to|under)\s+(.+)$",
        r"(?:add|create)\s+(?:a\s+)?bullet\s+(.+?)\s+(?:to|under|in)\s+(.+)$",
    ]
    
    DELETE_PATTERNS = [
        r"delete\s+(.+?)(?:\s+from\s+(.+))?$",
        r"remove\s+(.+?)(?:\s+from\s+(.+))?$",
        r"(?:delete|remove)\s+(?:the\s+)?(.+?)\s+(?:bullet|item)(?:\s+from\s+(.+))?$",
    ]
    
    FIND_PATTERNS = [
        r"find\s+(.+)",
        r"(?:what|where)\s+is\s+(.+)",
        r"search\s+(?:for\s+)?(.+)",
    ]
    
    BATCH_PATTERNS = [
        r"(?:change|update)\s+(?:all|every)\s+(.+?)\s+(?:from|which|that)\s+(.+?)\s+to\s+(.+)$",
        r"replace\s+(?:all|every)\s+(.+?)\s+(?:with|to)\s+(.+)$",
    ]

    REPLACE_PATTERNS = [
        r"replace\s+(?:the\s+)?section\s+(.+?)\s+(?:with|to)\s+(.+)$",
        r"replace\s+(.+?)\s+(?:with|to)\s+(.+)$",
    ]
    
    @staticmethod
    def parse_command(command: str) -> Optional[ParsedCommand]:
        """Parse a natural language command."""
        command = command.strip()
        command_lower = command.lower()
        
        # Try update patterns
        for pattern in CommandParser.UPDATE_PATTERNS:
            match = re.match(pattern, command_lower, re.IGNORECASE)
            if match:
                return ParsedCommand(
                    command_type="update",
                    target=match.group(1).strip(),
                    action="set",
                    value=match.group(2).strip(),
                    context={},
                    confidence=0.9,
                )
        
        # Try add patterns
        for pattern in CommandParser.ADD_PATTERNS:
            match = re.match(pattern, command_lower, re.IGNORECASE)
            if match:
                content = match.group(1).strip()
                section = match.group(2).strip() if len(match.groups()) > 1 else ""
                
                return ParsedCommand(
                    command_type="add",
                    target=section,
                    action="append_bullet",
                    value=content,
                    context={},
                    confidence=0.85,
                )
        
        # Try delete patterns
        for pattern in CommandParser.DELETE_PATTERNS:
            match = re.match(pattern, command_lower, re.IGNORECASE)
            if match:
                target = match.group(1).strip()
                context = match.group(2).strip() if len(match.groups()) > 1 and match.group(2) else ""
                
                return ParsedCommand(
                    command_type="delete",
                    target=target,
                    action="remove",
                    value="",
                    context={"context": context},
                    confidence=0.8,
                )
        
        # Try section replacement patterns
        for pattern in CommandParser.REPLACE_PATTERNS:
            match = re.match(pattern, command_lower, re.IGNORECASE)
            if match:
                target = match.group(1).strip()
                value = match.group(2).strip()
                return ParsedCommand(
                    command_type="replace",
                    target=target,
                    action="replace_section",
                    value=value,
                    context={},
                    confidence=0.88,
                )

        # Try batch patterns
        for pattern in CommandParser.BATCH_PATTERNS:
            match = re.match(pattern, command_lower, re.IGNORECASE)
            if match:
                if len(match.groups()) == 3:
                    # Pattern: change all X from Y to Z
                    target_type = match.group(1).strip()
                    old_value = match.group(2).strip()
                    new_value = match.group(3).strip()
                    
                    return ParsedCommand(
                        command_type="batch_update",
                        target=target_type,
                        action="batch_replace",
                        value=new_value,
                        context={"old_value": old_value},
                        confidence=0.85,
                    )
                elif len(match.groups()) == 2:
                    # Pattern: replace all X with Y
                    old_value = match.group(1).strip()
                    new_value = match.group(2).strip()
                    
                    return ParsedCommand(
                        command_type="batch_update",
                        target="all",
                        action="batch_replace",
                        value=new_value,
                        context={"old_value": old_value},
                        confidence=0.8,
                    )
        
        # Try find patterns
        for pattern in CommandParser.FIND_PATTERNS:
            match = re.match(pattern, command_lower, re.IGNORECASE)
            if match:
                return ParsedCommand(
                    command_type="find",
                    target=match.group(1).strip(),
                    action="search",
                    value="",
                    context={},
                    confidence=0.75,
                )
        
        return None


class CommandInterpreter:
    """Interprets parsed commands and generates operations."""
    
    def __init__(self, document_tree: Optional[DocumentTree] = None):
        self.document_tree = document_tree
        self.parser = CommandParser()
    
    def interpret_command(self, command: str) -> List[UpdateOperation]:
        """Interpret a command and generate operations."""
        parsed = self.parser.parse_command(command)
        
        if not parsed:
            return []
        
        if parsed.command_type == "update":
            return self._interpret_update(parsed)
        elif parsed.command_type == "replace":
            return self._interpret_replace(parsed)
        elif parsed.command_type == "add":
            return self._interpret_add(parsed)
        elif parsed.command_type == "delete":
            return self._interpret_delete(parsed)
        elif parsed.command_type == "batch_update":
            return self._interpret_batch_update(parsed)
        elif parsed.command_type == "find":
            return self._interpret_find(parsed)
        
        return []
    
    def _interpret_update(self, parsed: ParsedCommand) -> List[UpdateOperation]:
        """Interpret an update command."""
        if not self.document_tree:
            return []
        
        # Find the target in the document tree
        targets = self.document_tree.find_by_text(parsed.target, partial=True)
        
        operations = []
        for target_node in targets:
            op = OperationBuilder.update_text(
                target_id=target_node.element_id,
                target_path=target_node.element_id,
                old_value=target_node.text,
                new_value=parsed.value,
                description=f"Update {parsed.target} to {parsed.value}",
            )
            operations.append(op)
        
        return operations
    
    def _interpret_replace(self, parsed: ParsedCommand) -> List[UpdateOperation]:
        """Interpret a section replacement command."""
        if not self.document_tree:
            return []

        sections = self.document_tree.find_by_text(parsed.target, partial=True)
        operations = []

        for section_node in sections:
            if section_node.element_type != ElementType.SECTION:
                continue

            op = OperationBuilder.update_text(
                target_id=section_node.element_id,
                target_path=section_node.element_id,
                old_value=section_node.text,
                new_value=parsed.value,
                description=f"Replace section '{parsed.target}' with '{parsed.value}'",
            )
            operations.append(op)

        return operations

    def _interpret_add(self, parsed: ParsedCommand) -> List[UpdateOperation]:
        """Interpret an add/append command."""
        if not self.document_tree:
            return []
        
        # Find the target section
        sections = self.document_tree.find_by_text(parsed.target, partial=True)
        
        operations = []
        for section_node in sections:
            if section_node.element_type == ElementType.SECTION:
                # Generate an insert operation
                op = OperationBuilder.insert_text(
                    target_id=section_node.element_id,
                    target_path=section_node.element_id,
                    text=parsed.value,
                    description=f"Add bullet '{parsed.value}' to {parsed.target}",
                )
                op.operation_type = OperationType.INSERT_TEXT
                operations.append(op)
        
        return operations
    
    def _interpret_delete(self, parsed: ParsedCommand) -> List[UpdateOperation]:
        """Interpret a delete command."""
        if not self.document_tree:
            return []
        
        # Find the target to delete
        targets = self.document_tree.find_by_text(parsed.target, partial=True)
        
        operations = []
        for target_node in targets:
            # If context is provided, only delete if in that context
            if parsed.context.get("context"):
                parent = self.document_tree.get_parent(target_node.element_id)
                if parent and parsed.context["context"].lower() not in parent.text.lower():
                    continue
            
            op = OperationBuilder.delete_text(
                target_id=target_node.element_id,
                target_path=target_node.element_id,
                old_value=target_node.text,
                description=f"Delete '{parsed.target}'",
            )
            operations.append(op)
        
        return operations
    
    def _interpret_batch_update(self, parsed: ParsedCommand) -> List[UpdateOperation]:
        """Interpret a batch update command."""
        if not self.document_tree:
            return []
        
        old_value = parsed.context.get("old_value", "")
        new_value = parsed.value
        
        # Find all matching nodes
        targets = self.document_tree.find_by_text(old_value, partial=True)
        
        operations = []
        for target_node in targets:
            # Only update if type matches (if specified)
            if parsed.target != "all":
                if parsed.target.lower() not in target_node.element_type.value.lower():
                    continue
            
            op = OperationBuilder.update_text(
                target_id=target_node.element_id,
                target_path=target_node.element_id,
                old_value=old_value,
                new_value=new_value,
                description=f"Batch update: change '{old_value}' to '{new_value}'",
            )
            operations.append(op)
        
        return operations
    
    def _interpret_find(self, parsed: ParsedCommand) -> List[UpdateOperation]:
        """Interpret a find command (returns info, not operations)."""
        if not self.document_tree:
            return []
        
        # Find matching nodes
        targets = self.document_tree.find_by_text(parsed.target, partial=True)
        
        # This would typically be handled differently (not as operations)
        # but we can return an informational operation
        return []


class MultiCommandProcessor:
    """Processes multiple commands sequentially."""
    
    def __init__(self, document_tree: Optional[DocumentTree] = None):
        self.document_tree = document_tree
        self.interpreter = CommandInterpreter(document_tree)
        self.operations: List[UpdateOperation] = []
    
    def process_commands(self, commands: List[str]) -> List[UpdateOperation]:
        """Process a list of commands."""
        self.operations = []
        
        for command in commands:
            ops = self.interpreter.interpret_command(command)
            self.operations.extend(ops)
        
        return self.operations
    
    def get_combined_plan_description(self) -> str:
        """Get a description of all operations."""
        if not self.operations:
            return "No operations"
        
        descriptions = []
        for op in self.operations:
            descriptions.append(f"- {op.description}")
        
        return f"Total operations: {len(self.operations)}\n" + "\n".join(descriptions)
