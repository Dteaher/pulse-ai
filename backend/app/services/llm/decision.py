"""Exclusive ready/question wire branches; canonical process stays unchanged."""
from typing import Annotated, Literal
from pydantic import Field, model_validator
from ...models import StrictModel, ProcessDefinition, AmbiguityAnalysis


class ReadyDecision(StrictModel):
    status: Literal['ready']
    process: ProcessDefinition

    @model_validator(mode='after')
    def no_unanswered_critical_questions(self):
        if any(q.severity == 'critical' for q in self.process.ambiguities):
            raise ValueError('Ready cannot contain unanswered critical questions')
        return self


class QuestionDecision(StrictModel):
    status: Literal['clarification_required']
    analysis: AmbiguityAnalysis

    @model_validator(mode='after')
    def critical_question_required(self):
        if not any(q.severity == 'critical' for q in self.analysis.ambiguities):
            raise ValueError('Question branch requires a critical question')
        return self


class ParseDecision(StrictModel):
    result: Annotated[ReadyDecision | QuestionDecision, Field(discriminator='status')]
