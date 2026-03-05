from typing import List, Dict, Any, Optional
import httpx
from pathlib import Path
import json
from core.config import settings


class DocumentAnalyzer:
    """Extract document text and run Q&A via hired agent (no MLOPS inference)."""
    
    def __init__(self):
        # Optional MLOPS config kept for compatibility; inference uses hired agent only
        self.api_url = getattr(settings, "MLOPS_API_URL", None)
        self.model = getattr(settings, "MLOPS_MODEL", "gpt-4o-mini")
    
    async def read_document(self, file_path: str) -> str:
        """Read document content based on file type and extract text for inference endpoint"""
        path = Path(file_path)
        file_ext = path.suffix.lower()
        
        try:
            # Text-based files
            if file_ext in ['.txt', '.md', '.rtf']:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    return f.read()
            
            elif file_ext == '.csv':
                import csv
                content = []
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    reader = csv.reader(f)
                    for row in reader:
                        content.append(','.join(row))
                return '\n'.join(content)
            
            elif file_ext == '.json':
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return json.dumps(data, indent=2)
            
            elif file_ext == '.xml':
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    return f.read()
            
            # PDF files
            elif file_ext == '.pdf':
                try:
                    import PyPDF2
                    content = []
                    with open(path, 'rb') as f:
                        pdf_reader = PyPDF2.PdfReader(f)
                        for page_num, page in enumerate(pdf_reader.pages):
                            text = page.extract_text()
                            if text.strip():
                                content.append(f"--- Page {page_num + 1} ---\n{text}")
                    return '\n\n'.join(content) if content else "[PDF file contains no extractable text]"
                except ImportError:
                    return f"[PDF extraction requires PyPDF2 library. File: {path.name}]"
                except Exception as e:
                    return f"[Error extracting PDF content: {str(e)}]"
            
            # Word documents (.docx) - try python-docx first, then docx2txt fallback
            elif file_ext == '.docx':
                # Primary: python-docx (better structure for paragraphs/tables)
                try:
                    from docx import Document
                    doc = Document(path)
                    content = []
                    for paragraph in doc.paragraphs:
                        if paragraph.text.strip():
                            content.append(paragraph.text)
                    for table in doc.tables:
                        for row in table.rows:
                            row_text = ' | '.join([cell.text.strip() for cell in row.cells if cell.text.strip()])
                            if row_text:
                                content.append(row_text)
                    if content:
                        return '\n'.join(content)
                    # Empty body - try docx2txt which can get more content (headers, footers)
                except ImportError:
                    pass
                except Exception:
                    pass
                # Fallback: docx2txt (works without python-docx, extracts text from .docx)
                try:
                    import docx2txt
                    text = docx2txt.process(str(path))
                    return text.strip() if text and text.strip() else "[DOCX file contains no extractable text]"
                except ImportError:
                    return f"[DOCX extraction requires python-docx or docx2txt. File: {path.name}]"
                except Exception as e:
                    return f"[Error extracting DOCX content: {str(e)}]"
            
            # Legacy Word documents (.doc) - requires additional library
            elif file_ext == '.doc':
                try:
                    # Try using python-docx2txt or textract
                    import docx2txt
                    text = docx2txt.process(path)
                    return text if text.strip() else "[DOC file contains no extractable text]"
                except ImportError:
                    return f"[DOC extraction requires docx2txt library. File: {path.name}]"
                except Exception as e:
                    return f"[Error extracting DOC content: {str(e)}]"
            
            # Excel files (.xlsx, .xls)
            elif file_ext in ['.xlsx', '.xls']:
                try:
                    import pandas as pd
                    # Read all sheets
                    excel_file = pd.ExcelFile(path)
                    content = []
                    for sheet_name in excel_file.sheet_names:
                        df = pd.read_excel(path, sheet_name=sheet_name)
                        content.append(f"--- Sheet: {sheet_name} ---")
                        # Convert DataFrame to string representation
                        content.append(df.to_string(index=False))
                    return '\n\n'.join(content) if content else "[Excel file contains no data]"
                except ImportError:
                    return f"[Excel extraction requires pandas and openpyxl libraries. File: {path.name}]"
                except Exception as e:
                    return f"[Error extracting Excel content: {str(e)}]"
            
            # OpenDocument formats (.odt, .ods)
            elif file_ext == '.odt':
                try:
                    from odf import text, teletype
                    from odf.opendocument import load
                    doc = load(path)
                    paragraphs = doc.getElementsByType(text.P)
                    content = []
                    for para in paragraphs:
                        text_content = teletype.extractText(para)
                        if text_content.strip():
                            content.append(text_content)
                    return '\n'.join(content) if content else "[ODT file contains no extractable text]"
                except ImportError:
                    return f"[ODT extraction requires odfpy library. File: {path.name}]"
                except Exception as e:
                    return f"[Error extracting ODT content: {str(e)}]"
            
            elif file_ext == '.ods':
                try:
                    import pandas as pd
                    df = pd.read_excel(path, engine='odf')
                    return df.to_string(index=False) if not df.empty else "[ODS file contains no data]"
                except ImportError:
                    return f"[ODS extraction requires pandas and odfpy libraries. File: {path.name}]"
                except Exception as e:
                    return f"[Error extracting ODS content: {str(e)}]"
            
            else:
                return f"[Unsupported file type: {file_ext}. File: {path.name}]"
                
        except Exception as e:
            return f"[Error reading file {path.name}: {str(e)}]"
    
    async def analyze_documents_and_generate_questions(
        self, 
        documents: List[Dict[str, str]], 
        job_title: str,
        job_description: Optional[str] = None,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        *,
        agent_api_url: Optional[str] = None,
        agent_api_key: Optional[str] = None,
        agent_llm_model: Optional[str] = None,
        agent_temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Extract document text and optionally analyze via hired agent (no MLOPS).
        - When agent_api_url is provided: use hired agent for analysis and Q&A.
        - When not provided: only extract text; return extracted data and empty questions.
        """
        conversation_history = conversation_history or []
        job_title = str(job_title).strip() if job_title is not None else ""
        job_description = str(job_description).strip() if job_description is not None else None
        if job_description is not None and not job_description:
            job_description = None
        # Extract data from all documents (no inference)
        document_contents = []
        for doc in documents:
            path = doc.get("path")
            name = doc.get("name", "Unknown")
            if not path:
                continue
            try:
                content = await self.read_document(path)
            except Exception as e:
                content = f"[Error reading {name}: {str(e)}]"
            document_contents.append(f"=== {name} ===\n{content}\n")
        
        if not document_contents:
            return {
                "analysis": f"No documents could be read for job '{job_title}'.",
                "questions": [],
                "recommendations": [],
                "solutions": [],
                "next_steps": [],
                "raw_response": "",
            }
        all_content = "\n".join(document_contents)
        
        # No hired agent endpoint → extraction only (no MLOPS inference)
        if not (agent_api_url and (agent_api_url or "").strip()):
            return {
                "analysis": f"Document text extracted for job '{job_title}'. Select and assign agents to this job to enable AI-powered analysis and Q&A.",
                "questions": [],
                "recommendations": [],
                "solutions": [],
                "next_steps": [],
                "raw_response": "",
            }
        
        # Hired agent: build the prompt and call agent endpoint (OpenAI-compatible)
        system_prompt = """You are an expert AI assistant specialized in deeply understanding business requirements from documents and providing intelligent solutions.

Your primary tasks:
1. DEEP ANALYSIS: Thoroughly analyze all uploaded documents to extract:
   - Business objectives and goals
   - Technical requirements and specifications
   - Data structures, formats, and sources
   - Workflow processes and dependencies
   - Constraints, limitations, and edge cases
   - Success criteria and expected outcomes

2. INTELLIGENT QUESTIONING: Ask smart, contextual questions that:
   - Fill critical gaps in understanding
   - Clarify ambiguous requirements
   - Identify potential issues or risks
   - Understand business context and priorities
   - Focus on the most important aspects first

3. PROBLEM SOLVING: After receiving answers:
   - Synthesize all information (documents + answers)
   - Identify the core problem or need
   - Provide actionable solutions and recommendations
   - Suggest optimal approaches or workflows
   - Highlight potential challenges and mitigation strategies

4. SOLUTION-ORIENTED: Once you understand the problem:
   - Provide clear, actionable recommendations
   - Suggest specific AI agents or workflows that could solve the problem
   - Outline implementation steps
   - Identify key success factors

IMPORTANT: You must respond with valid JSON only. Format your response as JSON with this exact structure:
{
    "analysis": "Comprehensive analysis of requirements, extracted data, and understanding of the problem",
    "questions": ["Question 1", "Question 2", "Question 3"] (empty array if no questions needed),
    "recommendations": ["Actionable recommendation 1", "Recommendation 2"],
    "solutions": ["Proposed solution 1", "Solution 2"] (optional, provide when problem is understood),
    "next_steps": ["Step 1", "Step 2"] (optional, provide when ready to proceed)
}

When you have enough information to understand the problem, provide solutions and recommendations instead of asking more questions."""
        
        # Build user prompt
        job_desc_part = f'Job Description: {job_description}\n\n' if job_description else ''
        conv_part = ''
        if conversation_history:
            formatted_conv = self._format_conversation(conversation_history)
            conv_part = f'\n\nPrevious Conversation History:\n{formatted_conv}\n\n'
        
        user_prompt = f"""Job Title: {job_title}
{job_desc_part}=== UPLOADED DOCUMENTS WITH REQUIREMENTS ===
{all_content}
{conv_part}
TASK: 
1. Perform a DEEP and COMPREHENSIVE analysis of all documents above
2. Extract ALL requirements, data structures, workflows, and business objectives
3. Identify what the user needs to accomplish
4. Ask intelligent, targeted questions ONLY if critical information is missing
5. Once you understand the problem, provide SOLUTIONS and RECOMMENDATIONS

Be thorough, analytical, and solution-oriented. Focus on understanding the complete picture before asking questions."""
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        # Add conversation history if provided - only include Q&A pairs with both question and answer
        def _str_strip(val: Any) -> str:
            if val is None:
                return ""
            if isinstance(val, str):
                return val.strip()
            return str(val).strip()

        if conversation_history:
            for item in conversation_history:
                if item.get("type") == "question":
                    question = _str_strip(item.get("question"))
                    answer = _str_strip(item.get("answer"))
                    if question and answer:
                        messages.append({"role": "user", "content": question})
                        messages.append({"role": "assistant", "content": answer})
                elif item.get("type") == "analysis":
                    content = _str_strip(item.get("content"))
                    if content:
                        messages.append({"role": "assistant", "content": f"Analysis: {content}"})
        
        try:
            max_content_length = 50000
            for msg in messages:
                if len(msg.get("content", "")) > max_content_length:
                    msg["content"] = msg["content"][:max_content_length] + "\n\n[Content truncated due to length...]"
            
            model = (agent_llm_model or "").strip() or "gpt-4o-mini"
            temperature = agent_temperature if agent_temperature is not None else 0.7
            payload = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": 2000,
            }
            headers = {"Content-Type": "application/json"}
            if agent_api_key and (agent_api_key or "").strip():
                headers["Authorization"] = f"Bearer {(agent_api_key or '').strip()}"
            
            async with httpx.AsyncClient(timeout=120.0, verify=False) as client:
                response = await client.post(
                    agent_api_url.strip(),
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                result = response.json()
                raw_content = result["choices"][0]["message"].get("content")
                # OpenAI can return content as string or list of parts (e.g. multimodal)
                if isinstance(raw_content, list):
                    assistant_message = " ".join(
                        p.get("text", p.get("content", "")) if isinstance(p, dict) else str(p)
                        for p in raw_content
                    )
                elif isinstance(raw_content, str):
                    assistant_message = raw_content
                else:
                    assistant_message = str(raw_content or "")
                
                try:
                    parsed = json.loads(assistant_message)
                    return {
                        "analysis": parsed.get("analysis", ""),
                        "questions": parsed.get("questions", []),
                        "recommendations": parsed.get("recommendations", []),
                        "solutions": parsed.get("solutions", []),
                        "next_steps": parsed.get("next_steps", []),
                        "raw_response": assistant_message,
                    }
                except json.JSONDecodeError:
                    return {
                        "analysis": assistant_message,
                        "questions": self._extract_questions(assistant_message),
                        "recommendations": self._extract_recommendations(assistant_message),
                        "solutions": [],
                        "next_steps": [],
                        "raw_response": assistant_message,
                    }
        except httpx.HTTPStatusError as e:
            error_detail = f"Hired agent API error: {e.response.status_code}"
            try:
                error_detail += f" - {e.response.text[:500]}"
            except Exception:
                pass
            raise Exception(error_detail)
        except httpx.RequestError as e:
            raise Exception(f"Hired agent API request error: {str(e)}")
        except Exception as e:
            raise Exception(f"Error analyzing documents: {str(e)}")
    
    async def process_user_response(
        self,
        user_answer: str,
        documents: List[Dict[str, str]],
        job_title: str,
        job_description: Optional[str],
        conversation_history: List[Dict[str, str]],
        *,
        agent_api_url: Optional[str] = None,
        agent_api_key: Optional[str] = None,
        agent_llm_model: Optional[str] = None,
        agent_temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Process user's answer and generate follow-up questions or final recommendations via hired agent."""
        last_question = None
        for item in reversed(conversation_history):
            if item.get("type") == "question" and not item.get("answer"):
                last_question = item.get("question") or ""
                break
        
        updated_history = []
        answer_added = False
        for item in conversation_history:
            if item.get("type") == "question" and not item.get("answer") and not answer_added:
                updated_item = item.copy()
                updated_item["answer"] = user_answer
                updated_history.append(updated_item)
                answer_added = True
            else:
                updated_history.append(item)
        
        if not answer_added and last_question:
            updated_history.append({
                "type": "question",
                "question": last_question,
                "answer": user_answer
            })
        
        return await self.analyze_documents_and_generate_questions(
            documents,
            job_title,
            job_description,
            updated_history,
            agent_api_url=agent_api_url,
            agent_api_key=agent_api_key,
            agent_llm_model=agent_llm_model,
            agent_temperature=agent_temperature,
        )
    
    def _format_conversation(self, history: List[Dict[str, Any]]) -> str:
        """Format conversation history for the prompt"""
        formatted = []
        for i, item in enumerate(history, 1):
            q = item.get("question")
            a = item.get("answer")
            question = q.strip() if isinstance(q, str) else (str(q) if q else "")
            answer = a.strip() if isinstance(a, str) else (str(a) if a else "")
            if question or answer:
                if question:
                    formatted.append(f"Q{i}: {question}")
                if answer:
                    formatted.append(f"A{i}: {answer}")
        return "\n".join(formatted)
    
    def _extract_questions(self, text: str) -> List[str]:
        """Extract questions from text if JSON parsing fails"""
        import re
        # Look for lines ending with ?
        questions = re.findall(r'[^.!?]*\?', text)
        return [q.strip() for q in questions if q.strip()][:3]  # Limit to 3 questions
    
    def _extract_recommendations(self, text: str) -> List[str]:
        """Extract recommendations/solutions from text if JSON parsing fails"""
        import re
        recommendations = []
        # Look for bullet points, numbered lists, or lines starting with keywords
        lines = text.split('\n')
        for line in lines:
            line = line.strip()
            # Match bullet points, numbered items, or lines with recommendation keywords
            if (line.startswith('-') or line.startswith('*') or 
                re.match(r'^\d+[\.\)]', line) or
                any(keyword in line.lower() for keyword in ['recommend', 'suggest', 'solution', 'should', 'consider'])):
                # Clean up the line
                cleaned = re.sub(r'^[-*\d+\.\)]\s*', '', line)
                if cleaned and len(cleaned) > 10:  # Only include substantial recommendations
                    recommendations.append(cleaned)
        return recommendations[:5]  # Limit to 5 recommendations