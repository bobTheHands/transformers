# coding=utf-8
# Copyright 2021 The Facebook Inc. and The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tokenization class for Wav2Vec2Phoneme."""

import json
import os
import sys
from itertools import groupby
from typing import Any, Dict, List, Optional, Tuple, Union

from ...file_utils import requires_backends
from ...tokenization_utils import PreTrainedTokenizer, _insert_one_token_to_ordered_list
from ...tokenization_utils_base import AddedToken
from ...utils import logging


logger = logging.get_logger(__name__)


VOCAB_FILES_NAMES = {
    "vocab_file": "vocab.json",
    "tokenizer_config_file": "tokenizer_config.json",
}

PRETRAINED_VOCAB_FILES_MAP = {
    "vocab_file": {
        "facebook/wav2vec2-lv-60-espeak-cv-ft": "https://huggingface.co/facebook/wav2vec2-lv-60-espeak-cv-ft/resolve/main/vocab.json",
    },
    "tokenizer_config_file": {
        "facebook/wav2vec2-lv-60-espeak-cv-ft": "https://huggingface.co/facebook/wav2vec2-lv-60-espeak-cv-ft/resolve/main/tokenizer_config.json",
    },
}

# Wav2Vec2Phoneme has no max input length
PRETRAINED_POSITIONAL_EMBEDDINGS_SIZES = {"facebook/wav2vec2-lv-60-espeak-cv-ft": sys.maxsize}


class Wav2Vec2PhonemeCTCTokenizer(PreTrainedTokenizer):

    """
    Constructs a Wav2Vec2PhonemeCTC tokenizer.

    This tokenizer inherits from :class:`~transformers.PreTrainedTokenizer` which contains some of the main methods.
    Users should refer to the superclass for more information regarding such methods.

    Args:
        vocab_file (:obj:`str`):
            File containing the vocabulary.
        bos_token (:obj:`str`, `optional`, defaults to :obj:`"<s>"`):
            The beginning of sentence token.
        eos_token (:obj:`str`, `optional`, defaults to :obj:`"</s>"`):
            The end of sentence token.
        unk_token (:obj:`str`, `optional`, defaults to :obj:`"<unk>"`):
            The unknown token. A token that is not in the vocabulary cannot be converted to an ID and is set to be this
            token instead.
        pad_token (:obj:`str`, `optional`, defaults to :obj:`"<pad>"`):
            The token used for padding, for example when batching sequences of different lengths.
        do_phonemize (:obj:`bool`, `optional`, defaults to :obj:`True`):
            Whether the tokenizer should phonetize the input or not. Only if a sequence of phonemes is passed to the
            tokenizer, :obj:`do_phonemize` should be set to ``False``.
        phonemizer_lang (:obj:`str`, `optional`, defaults to :obj:`"en-us"`):
            The language of the phoneme set to which the tokenizer should phonetize the input text to.
        phonemizer_backend (:obj:`str`, `optional`. defaults to :obj:`"espeak"`):
            The backend phonetization library that shall be used by the phonemizer library. Defaults to ``espeak-ng``.
            See the `phonemizer package <https://github.com/bootphon/phonemizer#readme>`_. for more information.

        **kwargs
            Additional keyword arguments passed along to :class:`~transformers.PreTrainedTokenizer`
    """

    vocab_files_names = VOCAB_FILES_NAMES
    pretrained_vocab_files_map = PRETRAINED_VOCAB_FILES_MAP
    max_model_input_sizes = PRETRAINED_POSITIONAL_EMBEDDINGS_SIZES
    model_input_names = ["input_ids", "attention_mask"]

    def __init__(
        self,
        vocab_file,
        bos_token="<s>",
        eos_token="</s>",
        unk_token="<unk>",
        pad_token="<pad>",
        phone_delimiter_token=" ",
        word_delimiter_token=None,
        do_phonemize=True,
        phonemizer_lang="en-us",
        phonemizer_backend="espeak",
        **kwargs
    ):
        super().__init__(
            unk_token=unk_token,
            bos_token=bos_token,
            eos_token=eos_token,
            pad_token=pad_token,
            word_delimiter_token=word_delimiter_token,
            phone_delimiter_token=phone_delimiter_token,
            do_phonemize=do_phonemize,
            phonemizer_lang=phonemizer_lang,
            phonemizer_backend=phonemizer_backend,
            **kwargs,
        )

        self._word_delimiter_token = word_delimiter_token
        self._phone_delimiter_token = phone_delimiter_token
        self.do_phonemize = do_phonemize
        self.phonemizer_lang = phonemizer_lang
        self.phonemizer_backend = phonemizer_backend

        with open(vocab_file, encoding="utf-8") as vocab_handle:
            self.encoder = json.load(vocab_handle)
        self.decoder = {v: k for k, v in self.encoder.items()}

    @property
    def vocab_size(self) -> int:
        return len(self.decoder)

    def get_vocab(self) -> Dict:
        return dict(self.encoder, **self.added_tokens_encoder)

    def prepare_for_tokenization(
        self,
        text: str,
        is_split_into_words: bool = False,
        phonemizer_lang: Optional[str] = None,
        do_phonemize: Optional[bool] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Performs any necessary transformations before tokenization.

        This method should pop the arguments from kwargs and return the remaining :obj:`kwargs` as well. We test the
        :obj:`kwargs` at the end of the encoding process to be sure all the arguments have been used.

        Args:
            text (:obj:`str`):
                The text to prepare.
            is_split_into_words (:obj:`bool`, `optional`, defaults to :obj:`False`):
                Whether or not the input is already pre-tokenized (e.g., split into words). If set to :obj:`True`, the
                tokenizer assumes the input is already split into words (for instance, by splitting it on whitespace)
                which it will tokenize. This is useful for NER or token classification.
            phonemizer_lang (:obj:`str`, `optional`):
                The language of the phoneme set to which the tokenizer should phonetize the input text to.
            do_phonemize (:obj:`bool`, `optional`):
                Whether the tokenizer should phonetize the input text or not. Only if a sequence of phonemes is passed
                to the tokenizer, :obj:`do_phonemize` should be set to ``False``.


        Returns:
            :obj:`Tuple[str, Dict[str, Any]]`: The prepared text and the unused kwargs.
        """
        if is_split_into_words:
            text = " " + text

        # set whether tokenizer should phonemize or not
        if do_phonemize is not None:
            self.do_phonemize = do_phonemize

        # set the correct phonemizer language
        if phonemizer_lang is not None:
            self.phonemizer_lang = phonemizer_lang

        return (text, {})

    def _tokenize(self, text, **kwargs):
        """
        Converts a string in a sequence of tokens (string), using the tokenizer.
        """

        # make sure whitespace is stripped to prevent <unk>
        text = text.strip()

        # phonemize
        if self.do_phonemize:
            text = text.lower()

            # create list of phonemes
            text = self.phonemize(text, self.phonemizer_lang)

        # make sure ' ' is between phonemes
        tokens = text.split(" ")

        tokens = list(filter(lambda p: p.strip() != "", tokens))
        return tokens

    def phonemize(self, text: str, phonemizer_lang: Optional[str] = None) -> str:
        requires_backends(self, "phonemizer")

        from phonemizer import phonemize
        from phonemizer.separator import Separator

        word_delimiter = self.word_delimiter_token + " " if self.word_delimiter_token is not None else ""
        phonemizer_lang = phonemizer_lang if phonemizer_lang is not None else self.phonemizer_lang

        separator = Separator(phone=self.phone_delimiter_token, word=word_delimiter, syllable="")
        phonemes = phonemize(
            text,
            language=phonemizer_lang,
            backend=self.phonemizer_backend,
            separator=separator,
            language_switch="remove-flags",
        )
        phonemes = phonemes.strip()

        return phonemes

    @property
    def word_delimiter_token(self) -> str:
        """
        :obj:`str`: Word delimiter token. Log an error if used while not having been set.
        """
        if self._word_delimiter_token is None and self.verbose:
            return None
        return str(self._word_delimiter_token)

    @property
    def word_delimiter_token_id(self) -> Optional[int]:
        """
        :obj:`Optional[int]`: Id of the word_delimiter_token in the vocabulary. Returns :obj:`None` if the token has
        not been set.
        """
        if self._word_delimiter_token is None:
            return None
        return self.convert_tokens_to_ids(self.word_delimiter_token)

    @word_delimiter_token.setter
    def word_delimiter_token(self, value):
        self._word_delimiter_token = value

    @word_delimiter_token_id.setter
    def word_delimiter_token_id(self, value):
        self._word_delimiter_token = self.convert_tokens_to_ids(value)

    @property
    def phone_delimiter_token(self) -> str:
        """
        :obj:`str`: Word delimiter token. Log an error if used while not having been set.
        """
        if self._phone_delimiter_token is None and self.verbose:
            logger.error("Using phone_delimiter_token, but it is not set yet.")
            return None
        return str(self._phone_delimiter_token)

    @property
    def phone_delimiter_token_id(self) -> Optional[int]:
        """
        :obj:`Optional[int]`: Id of the phone_delimiter_token in the vocabulary. Returns :obj:`None` if the token has
        not been set.
        """
        if self._phone_delimiter_token is None:
            return None
        return self.convert_tokens_to_ids(self.phone_delimiter_token)

    @phone_delimiter_token.setter
    def phone_delimiter_token(self, value):
        self._phone_delimiter_token = value

    @phone_delimiter_token_id.setter
    def phone_delimiter_token_id(self, value):
        self._phone_delimiter_token = self.convert_tokens_to_ids(value)

    def _convert_token_to_id(self, token: str) -> int:
        """Converts a token (str) in an index (integer) using the vocab."""
        return self.encoder.get(token, self.encoder.get(self.unk_token))

    def _convert_id_to_token(self, index: int) -> str:
        """Converts an index (integer) in a token (str) using the vocab."""
        result = self.decoder.get(index, self.unk_token)
        return result

    def convert_tokens_to_string(
        self,
        tokens: List[str],
        group_tokens: bool = True,
        spaces_between_special_tokens: bool = False,
        filter_word_delimiter_token: bool = True,
    ) -> str:
        """
        Converts a connectionist-temporal-classification (CTC) output tokens into a single string.
        """
        # group same tokens into non-repeating tokens in CTC style decoding
        if group_tokens:
            tokens = [token_group[0] for token_group in groupby(tokens)]

        # filter self.pad_token which is used as CTC-blank token
        filtered_tokens = list(filter(lambda token: token != self.pad_token, tokens))

        # also filter self.word_delimiter_token if not not
        if filter_word_delimiter_token and self.word_delimiter_token is not None:
            filtered_tokens = list(filter(lambda token: token != self.word_delimiter_token, filtered_tokens))

        string = " ".join(filtered_tokens).strip()

        return string

    def _decode(
        self,
        token_ids: List[int],
        skip_special_tokens: bool = False,
        clean_up_tokenization_spaces: bool = True,
        group_tokens: bool = True,
        filter_word_delimiter_token: bool = True,
        spaces_between_special_tokens: bool = False,
    ) -> str:
        """
        special _decode function is needed for Wav2Vec2PhonemeTokenizer because added tokens should be treated exactly
        the same as tokens of the base vocabulary and therefore the function `convert_tokens_to_string` has to be
        called on the whole token list and not individually on added tokens
        """
        filtered_tokens = self.convert_ids_to_tokens(token_ids, skip_special_tokens=skip_special_tokens)

        result = []
        for token in filtered_tokens:
            if skip_special_tokens and token in self.all_special_ids:
                continue
            result.append(token)

        text = self.convert_tokens_to_string(
            result,
            group_tokens=group_tokens,
            spaces_between_special_tokens=spaces_between_special_tokens,
            filter_word_delimiter_token=filter_word_delimiter_token,
        )

        if clean_up_tokenization_spaces:
            clean_text = self.clean_up_tokenization(text)
            return clean_text
        else:
            return text

    def save_vocabulary(self, save_directory: str, filename_prefix: Optional[str] = None) -> Tuple[str]:
        if not os.path.isdir(save_directory):
            logger.error(f"Vocabulary path ({save_directory}) should be a directory")
            return
        vocab_file = os.path.join(
            save_directory, (filename_prefix + "-" if filename_prefix else "") + VOCAB_FILES_NAMES["vocab_file"]
        )

        with open(vocab_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(self.encoder, ensure_ascii=False))

        return (vocab_file,)

    def _add_tokens(self, new_tokens: Union[List[str], List[AddedToken]], special_tokens: bool = False) -> int:
        """
        Add a list of new tokens to the tokenizer class. If the new tokens are not in the vocabulary, they are added to
        it with indices starting from length of the current vocabulary.

        Args:
            new_tokens (:obj:`List[str]`or :obj:`List[tokenizers.AddedToken]`):
                Token(s) to add in vocabulary. A token is only added if it's not already in the vocabulary (tested by
                checking if the tokenizer assign the index of the ``unk_token`` to them).
            special_tokens (:obj:`bool`, `optional`, defaults to :obj:`False`):
                Whether or not the tokens should be added as special tokens.

        Returns:
            :obj:`int`: The number of tokens actually added to the vocabulary.

        Examples::

            # Let's see how to increase the vocabulary of Bert model and tokenizer
            tokenizer = Wav2Vec2PhonemeCTCTokenizer.from_pretrained('facebook/wav2vec2-lv-60-espeak-cv-ft')
            model = Wav2Vec2PhonemeForCTC.from_pretrained('facebook/wav2vec2-lv-60-espeak-cv-ft')

            num_added_toks = tokenizer.add_tokens(['new_tok1', 'my_new-tok2'])
            print('We have added', num_added_toks, 'tokens')
            # Note: resize_token_embeddings expects to receive the full size of the new vocabulary, i.e. the length of the tokenizer.
            model.resize_token_embeddings(len(tokenizer))
        """
        new_tokens = [str(tok) for tok in new_tokens]

        tokens_to_add = []
        for token in new_tokens:
            if not isinstance(token, str):
                raise ValueError(f"Token {token} has to be of type string, but is " f"of type {type(token)}.")
            assert isinstance(token, str)
            if (
                token != self.unk_token
                and self.convert_tokens_to_ids(token) == self.convert_tokens_to_ids(self.unk_token)
                and token not in tokens_to_add
            ):
                tokens_to_add.append(token)
                if self.verbose:
                    logger.info(f"Adding {token} to the vocabulary")

        added_tok_encoder = dict((tok, len(self) + i) for i, tok in enumerate(tokens_to_add))
        added_tok_decoder = {v: k for k, v in added_tok_encoder.items()}
        self.added_tokens_encoder.update(added_tok_encoder)
        self.added_tokens_decoder.update(added_tok_decoder)

        # Make sure we don't split on any special tokens (even they were already in the vocab before)
        for token in tokens_to_add:
            if len(token) > 1:
                self._additional_special_tokens.append(AddedToken(token))
                _insert_one_token_to_ordered_list(self.unique_no_split_tokens, token)

        self._create_trie(self.unique_no_split_tokens)

        return len(tokens_to_add)
